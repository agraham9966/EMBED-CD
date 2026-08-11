"""Map tools: brush-paint seeds (drag = many training pixels), and place-a-region click."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.core import Qgis
from qgis.gui import QgsMapTool, QgsMapToolEmitPoint, QgsRubberBand


def _line_geom_type():
    try:
        return Qgis.GeometryType.Line
    except AttributeError:
        from qgis.core import QgsWkbTypes
        return QgsWkbTypes.LineGeometry


class BrushTool(QgsMapTool):
    """Left-drag paints INCLUDE seeds, right-drag paints EXCLUDE seeds. A plain click still
    works (it's just a one-point stroke). Dragging is the point: one gesture yields hundreds
    of labelled pixels, which is what makes a real classifier trainable."""

    def __init__(self, canvas, on_stroke):
        super().__init__(canvas)
        self._canvas = canvas
        self._on_stroke = on_stroke
        self._pts = []
        self._negative = False
        self._band = None

    def canvasPressEvent(self, event):
        self._negative = event.button() == Qt.MouseButton.RightButton
        self._pts = [event.mapPoint()]
        self._band = QgsRubberBand(self._canvas, _line_geom_type())
        self._band.setColor(QColor(220, 40, 40) if self._negative else QColor(30, 200, 30))
        self._band.setWidth(3)
        self._band.addPoint(event.mapPoint())

    def canvasMoveEvent(self, event):
        if self._band is None:
            return
        p = event.mapPoint()
        self._pts.append(p)
        self._band.addPoint(p)

    def canvasReleaseEvent(self, event):
        if self._band is None:
            return
        pts = [(p.x(), p.y()) for p in self._pts]
        band, negative = self._band, self._negative
        self._band, self._pts = None, []
        self._on_stroke(pts, negative, band)   # dock takes ownership of the band


class PlaceTool(QgsMapToolEmitPoint):
    """One-shot: reports where the user clicks (project CRS) to drop a region box there."""
    def __init__(self, canvas, on_place):
        super().__init__(canvas)
        self._on_place = on_place

    def canvasReleaseEvent(self, event):
        p = event.mapPoint()
        self._on_place(p.x(), p.y())
