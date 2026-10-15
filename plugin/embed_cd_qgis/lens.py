"""A before/after lens: hover the map to see the before year's imagery under the cursor, hold A to
see the after year.

It brings its own imagery. Both years are EOX layers that are never added to the project, rendered
off-screen for the current view and painted only inside the circle, so it works whether or not the
user has a basemap open and leaves the layer tree untouched. It is a canvas ITEM, not a map tool,
so clicks still go to whatever tool is active — labelling keeps working with the lens on.
"""
import logging

from qgis.PyQt.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QTransform
from qgis.core import (
    QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry, QgsMapRendererParallelJob,
    QgsMapSettings, QgsPointXY, QgsProject, QgsRasterLayer,
)
from qgis.gui import QgsMapCanvasItem

from .compat import scoped as _scoped
from .engine import basemap as BM

log = logging.getLogger(__name__)

FLIP_KEY = _scoped(Qt, "Key", "Key_A")
_EV = {n: _scoped(QEvent, "Type", n) for n in (
    "MouseMove", "Leave", "Enter", "KeyPress", "KeyRelease", "ShortcutOverride", "Wheel",
    "FocusOut", "Resize")}
_SHIFT = _scoped(Qt, "KeyboardModifier", "ShiftModifier")


class CompareLens(QgsMapCanvasItem):
    """Paints the before (or, with A held, the after) year inside a circle at the cursor."""

    def __init__(self, canvas, outlines=lambda: None):
        super().__init__(canvas)
        self._canvas = canvas
        self.setZValue(1000)                     # above rubber bands and the highlight
        self.radius = 100
        self.years = (None, None)                # EOX years actually shown: (before, after)
        self._layers = {}                        # EOX year -> QgsRasterLayer (kept alive here)
        self._images = {}                        # EOX year -> (QImage, QgsRectangle it covers)
        self._jobs = []
        self._outlines = []                      # QPolygonF rings, map units of the canvas CRS
        self._outlines_of = outlines            # callable -> the objects layer, or None
        self._outline_img = None                 # (QImage, extent): outlines baked per view
        self._pos = None                         # cursor, scene coords; None = off the canvas
        self.after = False                       # A held
        self.active = False
        self._render_timer = QTimer()
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(150)      # debounce: one render per pan/zoom, not per step
        self._render_timer.timeout.connect(self._render)
        self._filter = _CanvasFilter(self)
        canvas.extentsChanged.connect(self._schedule)
        canvas.destinationCrsChanged.connect(self._schedule)
        self.setVisible(False)

    # ---------------- state ----------------
    def set_active(self, on):
        self.active = bool(on)
        self.setVisible(self.active)
        if self.active:
            self._canvas.installEventFilter(self._filter)
            self._canvas.viewport().installEventFilter(self._filter)
            self._render()
        else:
            self._canvas.removeEventFilter(self._filter)
            self._canvas.viewport().removeEventFilter(self._filter)
            self._cancel()
            self.after = False
        self.update()

    def set_years(self, before, after):
        years = (BM.nearest_year(before), BM.nearest_year(after))
        if years != self.years:
            self.years = years
            self._images.clear()
            if self.active:
                self._render()

    def set_radius(self, r):
        self.prepareGeometryChange()
        self.radius = int(r)
        self.update()

    def refresh(self):
        """Something outside changed (new objects, a new run): redraw from scratch."""
        self._schedule()

    def shown_year(self):
        return self.years[1 if self.after else 0]

    # ---------------- rendering ----------------
    def _layer(self, year):
        lyr = self._layers.get(year)
        if lyr is None:
            lyr = QgsRasterLayer(BM.xyz_uri(year), BM.layer_name(year), "wms")
            self._layers[year] = lyr
        return lyr if lyr.isValid() else None

    def _schedule(self, *_a):
        if self.active:
            self._render_timer.start()

    def _cancel(self):
        for job in self._jobs:
            job.cancelWithoutBlocking()
        self._jobs = []

    def _render(self):
        """Render both years for exactly the current view, off-screen. Both, not just the one in
        the lens: pressing A has to be instant, and the tiles are cached by QGIS anyway."""
        self._cancel()
        self._build_outlines()
        base = self._canvas.mapSettings()
        self._bake_outlines(base)
        for year in set(y for y in self.years if y is not None):
            lyr = self._layer(year)
            if lyr is None:
                continue
            ms = QgsMapSettings(base)
            ms.setLayers([lyr])
            job = QgsMapRendererParallelJob(ms)
            extent = ms.visibleExtent()
            job.finished.connect(lambda j=job, y=year, e=extent: self._rendered(j, y, e))
            self._jobs.append(job)
            job.start()

    def _rendered(self, job, year, extent):
        if job in self._jobs:
            self._jobs.remove(job)
        img = job.renderedImage()
        if not img.isNull():
            self._images[year] = (img, extent)
            self.update()

    def _bake_outlines(self, ms):
        """Draw the outlines ONCE per pan/zoom into a transparent overlay that both years share.
        Drawing them in paint() re-projected every polygon on every mouse move: measured 4.4 s
        per move with 9,000 objects zoomed out. Baked, a move is two small image copies."""
        self._outline_img = None
        if not self._outlines:
            return
        dpr = ms.devicePixelRatio() or 1.0
        size = ms.outputSize()
        img = QImage(int(size.width() * dpr), int(size.height() * dpr),
                     _scoped(QImage, "Format", "Format_ARGB32_Premultiplied"))
        img.setDevicePixelRatio(dpr)
        img.fill(0)
        ext = ms.visibleExtent()
        s = size.width() / ext.width()                          # logical px per map unit
        p = QPainter(img)
        p.setRenderHint(_scoped(QPainter, "RenderHint", "Antialiasing"))
        p.setTransform(QTransform().scale(s, -s).translate(-ext.xMinimum(), -ext.yMaximum()))
        pen = QPen(QColor(255, 255, 255, 230))
        pen.setWidthF(1.4)
        pen.setCosmetic(True)
        p.setPen(pen)
        # One polyline per ring, NOT one QPainterPath of all of them: stroking a single path of
        # 9,000 subpaths took 4 s; separately it is milliseconds per thousand.
        for ring in self._outlines:
            p.drawPolyline(ring)
        p.end()
        self._outline_img = (img, ext)

    def _build_outlines(self):
        """The objects' outlines, so you can see where an edge falls on the photo."""
        rings = []
        try:
            lyr = self._outlines_of()
            if lyr is not None and lyr.isValid():
                ms = self._canvas.mapSettings()
                prj = QgsProject.instance()
                ct = QgsCoordinateTransform(lyr.crs(), ms.destinationCrs(), prj)
                back = QgsCoordinateTransform(ms.destinationCrs(), lyr.crs(), prj)
                rect = back.transformBoundingBox(ms.visibleExtent())
                req = QgsFeatureRequest().setFilterRect(rect).setNoAttributes()
                for f in lyr.getFeatures(req):
                    g = QgsGeometry(f.geometry())
                    if g.isEmpty() or g.transform(ct) != 0:
                        continue
                    for part in g.asGeometryCollection():
                        rings.append(part.asQPolygonF())
        except RuntimeError:                     # the layer was deleted under us
            rings = []
        self._outlines = rings

    # ---------------- painting ----------------
    def boundingRect(self):
        s = self._canvas.size()
        return QRectF(0, 0, s.width(), s.height())

    def updatePosition(self):
        self.update()                            # we draw in view pixels; nothing to move

    def _lens_rect(self):
        if self._pos is None:
            return QRectF()
        r = self.radius + 30                     # room for the year tag on the rim
        return QRectF(self._pos.x() - r, self._pos.y() - r, 2 * r, 2 * r)

    def move_to(self, pos):
        old = self._lens_rect()
        self._pos = pos
        self.update(old.united(self._lens_rect()))

    def _draw_part(self, painter, image_extent, box):
        """Draw only the part of a view-sized image under `box`, wherever its extent now sits."""
        img, ext = image_extent
        tl = self.toCanvasCoordinates(QgsPointXY(ext.xMinimum(), ext.yMaximum()))
        br = self.toCanvasCoordinates(QgsPointXY(ext.xMaximum(), ext.yMinimum()))
        target = QRectF(tl, br)
        box = box.intersected(target)
        if box.isEmpty() or target.width() <= 0 or target.height() <= 0:
            return
        kx, ky = img.width() / target.width(), img.height() / target.height()
        src = QRectF((box.x() - target.x()) * kx, (box.y() - target.y()) * ky,
                     box.width() * kx, box.height() * ky)
        painter.drawImage(box, img, src)

    def paint(self, painter, *_a):
        if not self.active or self._pos is None:
            return
        circle = QPainterPath()
        circle.addEllipse(self._pos, self.radius, self.radius)
        painter.save()
        painter.setRenderHint(_scoped(QPainter, "RenderHint", "Antialiasing"))
        painter.setClipPath(circle)
        year = self.shown_year()
        got = self._images.get(year)
        if got is not None:
            self._draw_part(painter, got, circle.boundingRect())
        else:
            painter.fillPath(circle, QColor(40, 40, 40, 160))     # still loading
        if self._outline_img is not None:
            self._draw_part(painter, self._outline_img, circle.boundingRect())
        painter.restore()

        ring = QPen(QColor(255, 215, 0) if self.after else QColor(255, 255, 255))
        ring.setWidthF(2.0)
        painter.setPen(ring)
        painter.setBrush(_scoped(Qt, "BrushStyle", "NoBrush"))
        painter.drawEllipse(self._pos, self.radius, self.radius)
        if year is not None:
            tag = f"{year}  ·  {'after' if self.after else 'before (hold A: after)'}"
            fm = painter.fontMetrics()
            w, h = fm.horizontalAdvance(tag) + 10, fm.height() + 4
            box = QRectF(self._pos.x() - w / 2, self._pos.y() + self.radius + 4, w, h)
            painter.fillRect(box, QColor(0, 0, 0, 170))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(box, _scoped(Qt, "AlignmentFlag", "AlignCenter"), tag)

    def cleanup(self):
        self.set_active(False)
        try:
            self._canvas.extentsChanged.disconnect(self._schedule)
            self._canvas.destinationCrsChanged.disconnect(self._schedule)
        except (TypeError, RuntimeError):
            pass
        scene = self.scene()
        if scene is not None:
            scene.removeItem(self)


class _CanvasFilter(QObject):
    """Cursor tracking, the A key, and Shift+wheel for size — without becoming the map tool."""

    def __init__(self, lens):
        super().__init__()
        self.lens = lens

    def eventFilter(self, obj, ev):
        t = ev.type()
        lens = self.lens
        if t == _EV["MouseMove"]:
            pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
            lens.move_to(lens._canvas.mapToScene(pos))
        elif t == _EV["Leave"]:
            lens.move_to(None)
        elif t == _EV["Enter"]:
            lens._canvas.setFocus()              # so A reaches us without a click first
        elif t == _EV["ShortcutOverride"] and ev.key() == FLIP_KEY:
            ev.accept()                          # claim A before any QGIS shortcut does
            return True
        elif t in (_EV["KeyPress"], _EV["KeyRelease"]) and ev.key() == FLIP_KEY:
            if not ev.isAutoRepeat():
                lens.after = t == _EV["KeyPress"]
                lens.update(lens._lens_rect())
            return True
        elif t == _EV["FocusOut"] and lens.after:
            lens.after = False
            lens.update(lens._lens_rect())
        elif t == _EV["Wheel"] and ev.modifiers() & _SHIFT:
            d = ev.angleDelta()
            step = d.y() or d.x()                # Shift turns vertical wheel into horizontal
            lens.set_radius(max(40, min(400, lens.radius + (10 if step > 0 else -10))))
            lens.move_to(lens._pos)
            on_resize = getattr(lens, "on_radius_changed", None)
            if on_resize:
                on_resize(lens.radius)
            return True
        return False


def lens_icon(size=18):
    """A magnifier whose glass is split into a 'before' and an 'after' half. Drawn in code so
    there is no icon file to ship or find; flat, like QGIS's own toolbar icons."""
    from qgis.PyQt.QtGui import QIcon, QPixmap
    from qgis.PyQt.QtCore import QPointF
    scale = 4                                   # draw large, let Qt scale down crisply
    pm = QPixmap(size * scale, size * scale)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(_scoped(QPainter, "RenderHint", "Antialiasing"))
    p.scale(scale, scale)
    c, r = QPointF(7.4, 7.4), 6.6
    glass = QPainterPath()
    glass.addEllipse(c, r, r)
    p.setClipPath(glass)
    p.fillRect(QRectF(0, 0, 7.4, 18), QColor(76, 140, 74))      # before: vegetation
    p.fillRect(QRectF(7.4, 0, 10.6, 18), QColor(214, 170, 104))  # after: cleared
    p.setClipping(False)
    pen = QPen(QColor(70, 70, 70))
    pen.setWidthF(1.6)
    p.setPen(pen)
    p.setBrush(_scoped(Qt, "BrushStyle", "NoBrush"))
    p.drawEllipse(c, r, r)
    p.drawLine(QPointF(7.4, 0.8), QPointF(7.4, 14.0))
    pen.setWidthF(2.6)
    pen.setCapStyle(_scoped(Qt, "PenCapStyle", "RoundCap"))
    p.setPen(pen)
    p.drawLine(QPointF(12.4, 12.4), QPointF(17.0, 17.0))
    p.end()
    return QIcon(pm)
