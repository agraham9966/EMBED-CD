"""EMBED-CD dock: draw an area, pick two years, run.

The whole UI is one panel with ~8 controls. The job runs in a subprocess (PROJ/GDAL are
unsafe on QGIS's threads) and writes one small GeoTIFF per tile plus a VRT; the dock reloads
the VRT as tiles land, so the map fills in live.

The threshold is deliberately NOT part of the computation — the raster holds a continuous
change score, so moving the slider is pure symbology: instant, works mid-run, and survives
reopening the layer.
"""
import json
import os
import re
import shutil
import sys
import tempfile

from qgis.PyQt.QtCore import Qt, QProcess, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel,
    QSlider, QProgressBar, QLineEdit, QFileDialog, QScrollArea, QGroupBox, QMessageBox,
    QApplication,
)
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer,
    QgsRasterTransparency, Qgis, QgsField,
)

from .maptool import RectangleTool

try:
    from qgis.gui import QgsCollapsibleGroupBox as _GroupBox
except ImportError:
    _GroupBox = QGroupBox

_YEARS = [str(y) for y in range(2017, 2026)]
_NODATA = -1.0
_DETAIL = {"10 m (full)": 10.0, "20 m": 20.0, "50 m": 50.0, "100 m": 100.0}
_CELL_M = 160.0        # embedding cell size in GROUND metres; see embed_cd/cells.py
_TILE_KM = 10.24       # one COG block at 10 m — the unit everything is fetched in
# Both years of one tile, measured on a home connection. A coarse tile is the same 1024 px but
# reads from an overview, and costs ~11 s rather than ~4.5 — while covering up to 64x the ground,
# which is the entire point. Estimating both at the full-res number would badly overstate a big
# coarse job (and understate a small one).
_SEC_PER_TILE = 4.5
_SEC_PER_COARSE_TILE = 11.0


def _trim(pm):
    """Crop a pixmap to its non-transparent artwork.

    The logo carries a lot of empty margin — the marks occupy about two thirds of its height and
    half its width — so scaling the whole file to a title-bar height leaves the artwork tiny.
    The bounding box is found on a small copy (a few thousand pixels) and mapped back, which
    keeps this generic if the logo is ever replaced, and costs nothing at startup.
    """
    try:
        small = pm.scaled(96, 96, _scoped(Qt, "AspectRatioMode", "KeepAspectRatio"),
                          _scoped(Qt, "TransformationMode", "FastTransformation"))
        img = small.toImage()
        xs, ys = [], []
        for y in range(img.height()):
            for x in range(img.width()):
                if (img.pixel(x, y) >> 24) & 0xFF > 8:      # alpha
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return pm
        fx, fy = pm.width() / img.width(), pm.height() / img.height()
        x0, x1 = int(min(xs) * fx), int((max(xs) + 1) * fx)
        y0, y1 = int(min(ys) * fy), int((max(ys) + 1) * fy)
        return pm.copy(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
    except Exception:
        return pm


def _scoped(owner, category, name):
    """Enum that works on both PyQt5 (QGIS 3) and PyQt6 (QGIS 4)."""
    try:
        return getattr(getattr(owner, category), name)
    except AttributeError:
        return getattr(owner, name)


def _qvariant_double():
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QMetaType.Type.Double
    except (ImportError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        return QVariant.Double


def _qvariant_string():
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QMetaType.Type.QString
    except (ImportError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        return QVariant.String


class ChangeDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("EMBED-CD", iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()

        self.rect = None            # QgsRectangle in project CRS
        self.bbox = None            # (min_lon, min_lat, max_lon, max_lat)
        self.tool = None
        self.area_band = None      # the ONE persistent area outline
        self.proc = None
        self.out_dir = None
        self.vrt_path = None
        self.layer_id = None
        self.cov_layer_id = None    # thematic 'why is there no answer here' layer
        self.photo_ids = {}         # year -> layer id; several can be loaded at once
        self._current_group = None  # RESOLVED tree name (may carry a '(2)' suffix)
        self._current_base = None   # the name before disambiguation
        self._current_area_key = None  # WHICH AREA the tracked layers belong to
        self.n_tiles = 0
        self._canceled = False
        self._switching = False
        self._tmp_root = None
        # Each step folds itself ONCE, the first time it is finished with. Re-folding on every
        # refresh would fight anyone who reopened it to change something, which is worse than a
        # tall panel — the complaint auto-collapse usually earns.
        self._folded_once = set()
        # Every run made or opened this session. Without this the dock silently points at
        # whichever was last, while every area's layers sit in the tree looking equally live —
        # and a temp run's folder is a mkdtemp path nobody can navigate back to.
        self.runs = []

        self._thr_timer = QTimer(self)
        self._thr_timer.setSingleShot(True)
        self._thr_timer.setInterval(60)
        self._thr_timer.timeout.connect(self._apply_threshold)

        self._build_ui()
        self._build_title_bar()
        self._sync()

    def _build_title_bar(self):
        """Show the logo instead of the words EMBED-CD.

        A custom title-bar widget REPLACES the whole default bar, buttons included, so float and
        close have to be put back by hand — losing them to gain a logo would be a bad trade.
        """
        from qgis.PyQt.QtGui import QPixmap
        from qgis.PyQt.QtWidgets import QToolButton, QStyle

        from .plugin import icon_path

        path = icon_path()
        if not path:
            return                       # no logo installed: keep the ordinary text title
        pm = QPixmap(path)
        if pm.isNull():
            return
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 3, 2, 3)
        row.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(_trim(pm).scaledToHeight(
            26, _scoped(Qt, "TransformationMode", "SmoothTransformation")))
        logo.setToolTip("EMBED-CD")
        row.addWidget(logo)
        wordmark = QLabel("EMBED-CD")
        wordmark.setStyleSheet("font-weight: 700; letter-spacing: 1px;")
        row.addWidget(wordmark)
        row.addStretch(1)
        for std, slot, tip in (("SP_TitleBarNormalButton", self._toggle_float, "Dock / undock"),
                               ("SP_TitleBarCloseButton", self.close, "Close")):
            b = QToolButton()
            b.setAutoRaise(True)
            b.setIcon(self.style().standardIcon(_scoped(QStyle, "StandardPixmap", std)))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        self.setTitleBarWidget(bar)

    def show_change_raster(self, visible):
        """Tick or untick the change map in the layer tree.

        Once polygons exist they ARE the answer, and a continuous raster underneath them mostly
        fights the outlines for attention. Untick rather than remove, so it is one click back.
        """
        lid = self.layer_id
        if not lid:
            return
        node = QgsProject.instance().layerTreeRoot().findLayer(lid)
        if node is not None:
            node.setItemVisibilityChecked(bool(visible))
        if not visible:
            self._fold_once("step2", self.step2)

    def _toggle_float(self):
        self.setFloating(not self.isFloating())

    # ---------------- UI ----------------
    def _build_ui(self):
        outer = QWidget()
        lay = QVBoxLayout(outer)

        # Three numbered steps in native collapsible boxes. QGIS ships QgsCollapsibleGroupBox
        # and every QGIS panel uses it, so the folding reads as the application's own idiom
        # rather than something this plugin invented — and it needs no styling to look right in
        # any theme, which is the whole reason for choosing it over a hand-drawn header.
        arow = QHBoxLayout()
        self.area_lbl_sel = QLabel("Area:")
        arow.addWidget(self.area_lbl_sel)
        self.run_combo = QComboBox()
        self.run_combo.setToolTip(
            "Which run everything below applies to. Areas you make or open this session collect "
            "here, and switching brings back that area's threshold, objects and labels."
            + chr(10) * 2 +
            "Your classes travel with you; the labels on individual objects stay with their own "
            "area.")
        self.run_combo.currentIndexChanged.connect(self._switch_run)
        arow.addWidget(self.run_combo, 1)
        self.run_row = QWidget()
        self.run_row.setLayout(arow)
        arow.setContentsMargins(0, 0, 0, 0)
        self.run_row.setVisible(False)
        lay.addWidget(self.run_row)

        self.step1 = _GroupBox("1 · Area, years and output")
        lay.addWidget(self.step1)
        s1 = QVBoxLayout(self.step1)

        drow = QHBoxLayout()
        self.draw_btn = QPushButton("Draw area on map")
        self.draw_btn.setCheckable(True)
        self.draw_btn.setToolTip("Then drag a rectangle on the map to set the area.")
        self.draw_btn.clicked.connect(self._toggle_draw)
        drow.addWidget(self.draw_btn)
        self.clear_area_btn = QPushButton("Clear")
        self.clear_area_btn.setToolTip("Remove the area outline from the map.")
        self.clear_area_btn.clicked.connect(self._clear_area)
        drow.addWidget(self.clear_area_btn)
        s1.addLayout(drow)

        self.area_lbl = QLabel("Draw an area to begin.")
        self.area_lbl.setWordWrap(True)
        s1.addWidget(self.area_lbl)

        nrow = QHBoxLayout()
        nrow.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setToolTip(
            "What to call this area. Its layers go in a group of this name, so several areas "
            "can sit in the project at once without becoming a pile of 'change 2019→2024' "
            "entries. Leave it empty and the area's location is used instead.")
        self.name_edit.textChanged.connect(lambda _t: self._sync_name_placeholder())
        self.name_edit.editingFinished.connect(self._save_meta)
        nrow.addWidget(self.name_edit, 1)
        s1.addLayout(nrow)

        yrow = QHBoxLayout()
        yrow.addWidget(QLabel("From:"))
        self.year_a = QComboBox()
        self.year_a.addItems(_YEARS)
        self.year_a.setCurrentText("2019")
        yrow.addWidget(self.year_a)
        yrow.addWidget(QLabel("To:"))
        self.year_b = QComboBox()
        self.year_b.addItems(_YEARS)
        self.year_b.setCurrentText("2024")
        yrow.addWidget(self.year_b)
        yrow.addWidget(QLabel("Detail:"))
        self.detail = QComboBox()
        self.detail.addItems(list(_DETAIL))
        self.detail.setCurrentText("10 m (full)")
        self.detail.currentTextChanged.connect(
            lambda _t: self._describe_area() if self.bbox else None)
        for _c in (self.year_a, self.year_b):
            _c.currentTextChanged.connect(lambda _t: self._describe_dest())
        self.detail.setToolTip(
            "Output pixel size — and, above 10 m, how much gets downloaded. A coarser setting "
            "reads the data's own built-in reduced-resolution copies, so a large area takes "
            "minutes instead of hours (a 200x200 km job: ~59 GB at 10 m, ~2 GB at 100 m). The "
            "160 m embedding cells behind the classifier are identical either way, so coarse "
            "Detail still gives you polygons and classes. Coarse maps do read very slightly "
            "conservative near the cutoff. On a small area, use 10 m — a coarse setting there "
            "costs the same download and gives you a handful of pixels.")
        yrow.addWidget(self.detail)
        s1.addLayout(yrow)

        orow = QHBoxLayout()
        orow.addWidget(QLabel("Save to:"))
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("(temporary — results are discarded on exit)")
        self.out_edit.setToolTip("Optional. Set a folder to keep the results (and make the job "
                                 "resumable). Leave empty to work in a temp folder.")
        orow.addWidget(self.out_edit, 1)
        self.browse_btn = QPushButton("…")
        self.browse_btn.setMaximumWidth(30)
        self.browse_btn.clicked.connect(self._browse)
        orow.addWidget(self.browse_btn)
        self.open_btn = QPushButton("Open…")
        self.open_btn.setToolTip(
            "Reopen a change map you saved earlier. Pick the folder a previous run wrote to and "
            "everything comes back live: the threshold slider, polygons, and the classifier with "
            "its embeddings — not just the picture.")
        self.open_btn.clicked.connect(self._open_existing)
        orow.addWidget(self.open_btn)
        s1.addLayout(orow)

        # Where results land is easy to lose track of — a path set for one area is still set
        # when you draw the next one, and nothing on screen said so. This line always says
        # where the NEXT run will write, and carries the switch to the other mode, so neither
        # choice needs to be inferred from an empty text box.
        self.dest_lbl = QLabel("")
        self.dest_lbl.setWordWrap(True)
        self.dest_lbl.setStyleSheet("color: palette(mid); font-size: 10px;")
        self.dest_lbl.linkActivated.connect(self._dest_link)
        s1.addWidget(self.dest_lbl)
        self.out_edit.textChanged.connect(lambda _t: self._describe_dest())

        # Outside the fold: whatever else is collapsed, the action for this state stays put.
        rrow = QHBoxLayout()
        self.run_btn = QPushButton("Make change map")
        self.run_btn.setStyleSheet("font-weight: 600;")     # emphasis without a colour to clash
        self.run_btn.clicked.connect(self._run)
        rrow.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setVisible(False)
        rrow.addWidget(self.cancel_btn)
        lay.addLayout(rrow)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.step2 = _GroupBox("2 · Change map")
        lay.addWidget(self.step2)
        s2 = QVBoxLayout(self.step2)

        trow = QHBoxLayout()
        trow.addWidget(QLabel("Changed if ≥"))
        self.slider = QSlider(_scoped(Qt, "Orientation", "Horizontal"))
        self.slider.setRange(0, 100)
        self.slider.setValue(20)
        self.slider.valueChanged.connect(self._on_threshold)
        trow.addWidget(self.slider)
        self.thr_lbl = QLabel("0.20")
        trow.addWidget(self.thr_lbl)
        self.auto_btn = QPushButton("Auto")
        self.auto_btn.setToolTip("Cutoff chosen automatically for this scene (Otsu).")
        self.auto_btn.clicked.connect(self._auto)
        trow.addWidget(self.auto_btn)
        s2.addLayout(trow)

        # Ground truth: every year there are embeddings for, as a strip of small toggles. Binding
        # this to the two chosen years would have been the obvious thing and the wrong one —
        # confirming WHEN something changed means scrubbing across years, not just the two ends.
        # Two-digit labels because nine four-digit buttons do not fit a docked panel.
        prow = QHBoxLayout()
        prow.setSpacing(2)
        lbl = QLabel("Photo:")
        lbl.setToolTip("Sentinel-2 cloudless imagery (EOX), clipped to your area, one year per "
                       "button — so you can check what the change map is claiming, and see which "
                       "year a change actually appeared in.\n\nOne at a time: clicking another "
                       "year swaps it instantly, which is what makes the comparison readable. "
                       "Click the active year to turn it off.")
        prow.addWidget(lbl)
        self.photo_btns = {}
        for y in (int(v) for v in _YEARS):
            b = QPushButton(f"{y % 100:02d}")
            b.setCheckable(True)
            b.setFixedWidth(28)
            b.clicked.connect(lambda _c, yr=y: self._toggle_photo(yr))
            prow.addWidget(b)
            self.photo_btns[y] = b
        prow.addStretch(1)
        s2.addLayout(prow)
        from .engine import basemap as _BM
        credit = QLabel(f"{_BM.ATTRIBUTION}  ·  {_BM.LICENCE}")
        credit.setWordWrap(True)
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet("color: palette(mid); font-size: 9px;")
        s2.addWidget(credit)
        self._sync_photos()

        # Both are exports, both are rare, and "Polygonize" sat one row from the classifier's
        # "Make polygons" doing something different under a near-identical name. Tucked away
        # together, and the classifier's version renamed, that collision disappears.
        self.export_group = _GroupBox("Export")
        eg = QVBoxLayout(self.export_group)
        erow = QHBoxLayout()
        self.poly_btn = QPushButton("Plain polygons")
        self.poly_btn.setToolTip("Outlines of the changed area, with no embeddings attached — "
                                 "for when you only want the shapes. The classifier's "
                                 "'Find objects' is the one that can be labelled.")
        self.poly_btn.clicked.connect(self._polygonize)
        erow.addWidget(self.poly_btn)
        self.save_btn = QPushButton("Save as GeoTIFF…")
        self.save_btn.setToolTip("Merge the tiles into a single file.")
        self.save_btn.clicked.connect(self._save_geotiff)
        erow.addWidget(self.save_btn)
        eg.addLayout(erow)
        s2.addWidget(self.export_group)
        if hasattr(self.export_group, "setCollapsed"):
            self.export_group.setCollapsed(True)

        # Stays collapsed and disabled until a change map exists. Someone who only wants a
        # change map should never have to look at any of this.
        self.classify_group = _GroupBox("3 · Objects and classes")
        cl = QVBoxLayout(self.classify_group)
        from .classify import ClassifyPanel
        self.classify = ClassifyPanel(self, self.iface)
        cl.addWidget(self.classify)
        lay.addWidget(self.classify_group)
        if hasattr(self.classify_group, "setCollapsed"):
            self.classify_group.setCollapsed(True)

        g = _GroupBox("How it works")
        gl = QVBoxLayout(g)
        info = QLabel(
            "Each 10 m pixel carries an AlphaEarth embedding summarizing a whole year of "
            "satellite observation. Change is the distance between the two years' "
            "signatures — so it catches changes in behaviour, not just colour. The scale is "
            "absolute (0–1), so tiles are comparable and the threshold means the same thing "
            "everywhere.\n\nTwo layers are produced. The change map shows where the surface "
            "changed. The 'data coverage' layer underneath shows where no answer was possible "
            "— anything you can see on it (missing year, no tile) is a place the change map is "
            "silent about. If a spot is blank on BOTH, it was surveyed and did not change."
            "\n\nData: the AlphaEarth Foundations Satellite Embedding dataset, produced by "
            "Google and Google DeepMind (CC-BY 4.0). Every year 2017–2025 is covered globally, "
            "and it is read straight from public cloud storage — no account or API key.")
        info.setWordWrap(True)
        gl.addWidget(info)
        lay.addWidget(g)
        if hasattr(g, "setCollapsed"):
            g.setCollapsed(True)

        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(outer)
        self.setWidget(scroll)

    def _empty_group(self, name):
        """Remove the layers inside a group without removing the group itself.

        Returning to an area rebuilds its raster, coverage and polygons from disk. Leaving the
        previous copies in place would stack a second identical set every time you switched
        back — measured, six layers in a group that should hold three.
        """
        if not name:
            return
        root = QgsProject.instance().layerTreeRoot()
        g = root.findGroup(name)
        if g is None:
            return
        for lid in [c.layerId() for c in g.children() if hasattr(c, "layerId")]:
            try:
                QgsProject.instance().removeMapLayer(lid)
            except Exception:
                pass

    def _register_run(self):
        """Remember the run the dock is currently pointing at, keyed by its layer group."""
        if not self.vrt_path or not self.out_dir:
            return
        entry = {"group": self._current_group, "name": self.name_edit.text().strip(),
                 "key": self._area_key() if self.bbox is not None else None,
                 "auto": self._auto_name(), "out_dir": self.out_dir, "vrt": self.vrt_path,
                 "ya": self.year_a.currentText(), "yb": self.year_b.currentText(),
                 "detail": self.detail.currentText(), "bbox": self.bbox,
                 "temp": bool(self._tmp_root and os.path.abspath(self.out_dir).startswith(
                     os.path.abspath(self._tmp_root)))}
        for i, r in enumerate(self.runs):
            if r["out_dir"] == entry["out_dir"]:
                self.runs[i] = entry
                break
        else:
            self.runs.append(entry)
        self._refresh_run_combo()

    def _refresh_run_combo(self):
        if getattr(self, "run_combo", None) is None:
            return
        self._switching = True
        try:
            self.run_combo.clear()
            for r in self.runs:
                label = (f"{r['name'] or r['auto']} · {r['ya']}→{r['yb']} · {r['detail']}"
                         + ("  (temporary)" if r["temp"] else ""))
                self.run_combo.addItem(label, r["out_dir"])
            cur = self.run_combo.findData(self.out_dir)
            if cur >= 0:
                self.run_combo.setCurrentIndex(cur)
        finally:
            self._switching = False
        self.run_row.setVisible(len(self.runs) > 1)

    def _switch_run(self, _i):
        """Point the whole dock at another run: raster, threshold, objects, labels.

        Switching is a read, like opening — it must not disturb the layers of the area being
        left, which is why it detaches rather than removes. The classifier's banked classes come
        WITH you (they are your training, and applying them to another area is the point);
        per-polygon labels stay with the run they belong to and are reloaded from its folder.
        """
        if getattr(self, "_switching", False) or self.proc is not None:
            return
        out_dir = self.run_combo.currentData()
        entry = next((r for r in self.runs if r["out_dir"] == out_dir), None)
        if entry is None or entry["out_dir"] == self.out_dir:
            return
        self._switching = True
        try:
            for w, v in ((self.year_a, entry["ya"]), (self.year_b, entry["yb"]),
                         (self.detail, entry["detail"])):
                w.blockSignals(True)
                w.setCurrentText(v)
                w.blockSignals(False)
            self.name_edit.blockSignals(True)
            self.name_edit.setText(entry["name"])
            self.name_edit.blockSignals(False)
            self.bbox = entry["bbox"]
            self.out_dir, self.vrt_path = entry["out_dir"], entry["vrt"]
            # Detach by hand rather than via _release_layers: that RESOLVES a group name, and
            # the target group already exists, so it would invent "Area A (2)" and stack a
            # second copy of every layer in it. Here the group is known — we are returning to it.
            self.layer_id = self.cov_layer_id = None
            if getattr(self, "classify", None) is not None:
                self.classify.detach()
            self._current_group = entry["group"] or self._group_name()
            self._current_base = self._group_name()
            self._current_area_key = self._area_key()
            self._empty_group(self._current_group)   # its layers are about to be rebuilt
            self._refresh_layer()
            self._describe_area()
        finally:
            self._switching = False
        restored = None
        if getattr(self, "classify", None) is not None:
            try:
                restored = self.classify.restore()
            except Exception as exc:
                restored = f"Could not restore: {exc}"
        self.status.setText(f"Now working on {entry['name'] or entry['auto']}."
                            + (f"  {restored}" if restored else ""))
        self._sync()

    def _fold_once(self, key, box, collapsed=True):
        if key in self._folded_once or not hasattr(box, "setCollapsed"):
            return
        self._folded_once.add(key)
        box.setCollapsed(collapsed)

    def _step_summary(self):
        """Titles carry the answer once a step is folded, so a collapsed box still tells you
        what it is holding — without that, folding hides the settings AND the record of them."""
        if getattr(self, "step1", None) is None:
            return
        if self.bbox is None:
            self.step1.setTitle("1 · Area, years and output")
        else:
            name = self.name_edit.text().strip() or self._auto_name()
            self.step1.setTitle(
                f"1 · {name} — {self.year_a.currentText()}→{self.year_b.currentText()}, "
                f"{self.detail.currentText()}")
        self.step2.setTitle("2 · Change map" if self.layer_id is None else
                            f"2 · Change map — cutoff {self._threshold():.2f}")

    def _sync(self):
        running = self.proc is not None
        has_area = self.bbox is not None
        has_result = self.layer_id is not None
        self._step_summary()
        # NOT disabling step 2 wholesale: the photo strip lives there and is streamed global
        # imagery, useful for looking at an area before any run exists. The widgets that really
        # need a result — threshold, Auto, the exports — are gated individually below.
        if has_result:
            self._fold_once("step1", self.step1)
        self.run_btn.setEnabled(has_area and not running)
        self.run_btn.setToolTip("" if has_area else "Draw an area first.")
        self.cancel_btn.setVisible(running)
        self.draw_btn.setEnabled(not running)
        self.clear_area_btn.setEnabled(
            not running and (has_area or self.area_band is not None))
        self.browse_btn.setEnabled(not running)
        self.open_btn.setEnabled(not running)
        self._sync_photos()
        self._sync_name_placeholder()
        # The destination depends on the folder, the AREA and the years, so it has to be
        # recomputed here rather than only when the path box is edited — drawing a new area
        # changes where the next run lands, which is exactly the case that caused trouble.
        self._describe_dest()
        for w in (self.slider, self.auto_btn, self.poly_btn, self.save_btn):
            w.setEnabled(has_result)
        if getattr(self, "classify", None) is not None:
            self.classify_group.setEnabled(has_result)
            self.classify.sync()

    # ---------------- area ----------------
    def _toggle_draw(self):
        if self.draw_btn.isChecked():
            if self.tool is None:               # one tool for the life of the dock
                self.tool = RectangleTool(self.canvas, self._on_area)
            self.canvas.setMapTool(self.tool)
            self.status.setText("Drag a rectangle on the map.")
        elif self.tool is not None:
            self.canvas.unsetMapTool(self.tool)

    def _show_area_band(self, rect):
        """Draw (or move) the single outline that marks the chosen area."""
        from qgis.PyQt.QtGui import QColor as _QC
        from qgis.core import QgsGeometry
        from qgis.gui import QgsRubberBand
        from .maptool import polygon_geomtype
        if self.area_band is None:
            self.area_band = QgsRubberBand(self.canvas, polygon_geomtype())
            self.area_band.setColor(_QC(255, 140, 0))
            self.area_band.setFillColor(_QC(0, 0, 0, 0))
            self.area_band.setWidth(2)
        self.area_band.setToGeometry(QgsGeometry.fromRect(rect), None)

    def _clear_area_band(self):
        if self.area_band is not None:
            self.canvas.scene().removeItem(self.area_band)
            self.area_band = None

    def _clear_area(self):
        """Explicitly forget the drawn area and its outline."""
        self._clear_area_band()
        if self.tool is not None:
            self.tool.clear()
        self.rect = self.bbox = None
        self.draw_btn.setChecked(False)
        if self.tool is not None:
            self.canvas.unsetMapTool(self.tool)
        self.area_lbl.setText("Draw an area to begin.")
        self.status.setText("")
        self._sync()

    def _on_area(self, rect):
        self.rect = rect
        src = QgsProject.instance().crs()
        dst = QgsCoordinateReferenceSystem("EPSG:4326")
        r = (QgsCoordinateTransform(src, dst, QgsProject.instance()).transformBoundingBox(rect)
             if src != dst else rect)
        self.bbox = (r.xMinimum(), r.yMinimum(), r.xMaximum(), r.yMaximum())
        self.draw_btn.setChecked(False)
        if self.tool is not None:
            self.canvas.unsetMapTool(self.tool)
        self._show_area_band(rect)          # replaces any previous outline
        self._folded_once.discard("step1")  # drawing a new area makes step 1 live again
        if hasattr(self.step1, "setCollapsed"):
            self.step1.setCollapsed(False)
        self._describe_area()
        self._sync()

    def _estimate(self):
        """What this area will cost, before committing to it. Measured constants:
        a tile is 1024 source px and each tile-year is ~67 MB of embeddings.

        Detail now genuinely changes the bill. A coarse job reads a built-in overview, so a
        tile still costs ~67 MB but covers `factor` times more ground on each side — 10.24 km
        at 10 m, 81.92 km at 100 m. That is why a huge area at coarse Detail is minutes rather
        than hours.
        """
        import math
        from .engine import source as SRC
        lo, la, hi, ha = self.bbox
        w = (hi - lo) * 111.32 * math.cos(math.radians((la + ha) / 2))
        h = (ha - la) * 110.57
        res = _DETAIL[self.detail.currentText()]
        factor = SRC.factor_for(res)
        tile_km = _TILE_KM * factor
        # +1 per axis: the area is not aligned to the COG block grid, so a rectangle that is
        # nominally one tile wide almost always straddles two. Measured — an 8x9 km box needs
        # 4 tiles, a 16x16 km box needs 9. Better to overestimate the bill than surprise you.
        tiles = (math.ceil(w / tile_km) + 1) * (math.ceil(h / tile_km) + 1)
        px = (w * 1000 / res) * (h * 1000 / res)
        across = min(w, h) * 1000 / res         # pixels along the SHORT side of the output
        return {"w": w, "h": h, "tiles": tiles, "factor": factor,
                "src_m": SRC.NATIVE_RES * factor,
                "gb": tiles * 2 * 67e6 / 1e9,
                "minutes": tiles * (_SEC_PER_TILE if factor == 1
                                    else _SEC_PER_COARSE_TILE) / 60.0,
                "out_px": px, "across": across,
                # polygonize holds the change band, its int32 labels and a bool mask at once
                "poly_gb": px * 4 * 2.25 / 1e9}

    def _describe_area(self):
        e = self._estimate()
        msg = (f"Area ~{e['w']:.0f}×{e['h']:.0f} km · ~{e['tiles']} tiles · "
               f"~{e['gb']:.1f} GB to read · ~{e['minutes']:.0f} min · "
               f"output {e['out_px'] / 1e6:.1f} Mpx")
        # Too few pixels to be a map. At 100 m a 0.5 km box is 5x5 px, every polygon is a 1 ha
        # square, and it still costs a whole block to download — you pay full price for nothing.
        if e["across"] < 50:
            msg += (f" ⚠ only {e['across']:.0f} px across at this Detail — "
                    f"use a finer Detail or draw a bigger area.")
        elif e["across"] < 200:
            msg += f" ⚠ {e['across']:.0f} px across — polygons will be blocky."
        else:
            msg += ". Pick years, then Make change map."
        self.area_lbl.setText(msg)

    # ---------------- run ----------------
    def _dest_link(self, href):
        if href == "temp":
            self.out_edit.clear()
        else:
            self._browse()

    def _describe_dest(self):
        """Always visible, always current: where the NEXT run writes."""
        chosen = self.out_edit.text().strip()
        if chosen:
            run = ""
            if self.bbox is not None:
                ya, yb = self.year_a.currentText(), self.year_b.currentText()
                run = os.sep + f"change_{ya}_{yb}_{self._area_key()}"
            self.dest_lbl.setText(
                f"Results → {chosen}{run}  ·  <a href='temp'>use a temporary folder instead</a>")
        else:
            self.dest_lbl.setText(
                "Results → a temporary folder, discarded when QGIS closes  ·  "
                "<a href='pick'>keep them in a folder</a>")

    _SETTING_DIR = "embed_cd/last_dir"

    def _last_dir(self):
        """QGIS opens file dialogs in its own bin directory otherwise, which is never where
        anyone's data is."""
        try:
            from qgis.core import QgsSettings
            return QgsSettings().value(self._SETTING_DIR, "") or ""
        except Exception:
            return ""

    def _remember_dir(self, path):
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(self._SETTING_DIR, path)
        except Exception:
            pass

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the change map", self._last_dir())
        if d:
            self.out_edit.setText(d)
            self._remember_dir(d)

    _RUN_VRT = re.compile(r"^change_(\d{4})_(\d{4})\.vrt$")

    def _auto_name(self):
        """A name for an area nobody named: where it is. Two runs of the same years are
        otherwise indistinguishable in the layer tree, which is the problem groups exist to
        solve."""
        if self.bbox is None:
            return "area"
        lo, la, hi, ha = self.bbox
        cy, cx = (la + ha) / 2.0, (lo + hi) / 2.0
        return (f"{abs(cy):.2f}{'N' if cy >= 0 else 'S'} "
                f"{abs(cx):.2f}{'E' if cx >= 0 else 'W'}")

    def _sync_name_placeholder(self):
        if getattr(self, "name_edit", None) is not None:
            self.name_edit.setPlaceholderText(self._auto_name())

    def _group_name(self):
        name = self.name_edit.text().strip() if getattr(self, "name_edit", None) else ""
        return (f"{name or self._auto_name()}  "
                f"{self.year_a.currentText()}→{self.year_b.currentText()}")

    def _save_meta(self):
        """The name lives in the run folder, because nothing else on disk carries it: years come
        from the VRT filename and the extent from the raster, but a name is only in the user's
        head until it is written down.

        It is only applied to a run covering the area CURRENTLY drawn. Draw a new area, type a
        name for it, and `out_dir` still points at the run you just left — so matching on that
        alone renamed the previous area instead, and both then showed the same name in the Area
        list. Typing a name before running belongs to the run about to be made.
        """
        if not self.out_dir or not os.path.isdir(self.out_dir):
            return
        name = self.name_edit.text().strip()
        key = self._area_key() if self.bbox is not None else None
        current = next((r for r in self.runs if r["out_dir"] == self.out_dir), None)
        if current is not None and current.get("key") not in (None, key):
            return                      # the drawn area has moved on; this name is for the next run
        try:
            from .engine import store as ST
            ST.save_meta(self.out_dir, name=name)
        except Exception:
            pass
        if current is not None:
            current["name"] = name
        self._refresh_run_combo()

    def _resolve_group(self, base):
        """A tree name for a NEW area that is not already taken.

        Draw a second area and forget to change the Name and both would land in one group,
        silently interleaving two areas' layers — the exact thing groups exist to prevent. So
        the second becomes "name (2)".
        """
        root = QgsProject.instance().layerTreeRoot()
        if root.findGroup(base) is None:
            return base
        n = 2
        while root.findGroup(f"{base} ({n})") is not None:
            n += 1
        return f"{base} ({n})"

    def _group(self):
        """The layer-tree group for this run, created at the top if it does not exist.

        Everything an area produces — change map, coverage, polygons — belongs together. Without
        this, two areas in one project are an undifferentiated stack of near-identical layer
        names, and it is not obvious which belongs to what.
        """
        root = QgsProject.instance().layerTreeRoot()
        if not self._current_group:
            self._current_group = self._resolve_group(self._group_name())
        g = root.findGroup(self._current_group)
        # `found or insert(...)` looks natural and is wrong: an EMPTY QgsLayerTreeGroup is
        # falsy, so the moment a group had its layers cleared — which is exactly what returning
        # to an area does — the `or` fell through and built a second group beside it. Compare
        # against None.
        return g if g is not None else root.insertGroup(0, self._current_group)

    def _add_to_group(self, layer, bottom=False):
        QgsProject.instance().addMapLayer(layer, False)
        g = self._group()
        g.insertLayer(-1 if bottom else 0, layer)
        return layer

    def _area_key(self):
        """Short digest of the drawn area, so a run folder names WHERE as well as WHEN.
        4 decimal places is ~11 m, fine enough to separate two areas and exact enough that
        re-running the same bbox resumes instead of starting over."""
        import hashlib
        return hashlib.sha1(("%.4f_%.4f_%.4f_%.4f" % tuple(self.bbox)).encode()).hexdigest()[:6]

    def _find_runs(self, folder):
        """Every saved run at `folder` or one level below, newest first.

        Forgiving about which folder gets picked, because `Save to:` writes a `change_<a>_<b>`
        subfolder and it is a coin flip whether someone points at that or at its parent — and a
        parent quite reasonably holds SEVERAL runs, e.g. the same area compared across different
        year pairs.

        The name must match EXACTLY: a finished run leaves both `change_2019_2025.vrt` and a
        superseded revision `change_2019_2025.v4.vrt`, and the revision holds fewer tiles.
        """
        out = []
        for d in [folder] + [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                             if os.path.isdir(os.path.join(folder, n))]:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                m = self._RUN_VRT.match(n)
                if m:
                    p = os.path.join(d, n)
                    out.append({"path": p, "year_a": int(m.group(1)), "year_b": int(m.group(2)),
                                "mtime": os.path.getmtime(p),
                                "cells": len([f for f in names if f.startswith("cells_")])})
        return sorted(out, key=lambda r: -r["mtime"])

    def _choose_run(self, runs):
        """One run opens straight away; several have to be asked about.

        Silently taking the newest is the wrong default here — a folder holding 2019-2023 and
        2019-2024 gives no clue which one you got, and both are equally valid answers to
        'open the results in this folder'.
        """
        from qgis.PyQt.QtWidgets import QInputDialog
        from .engine import gdalio as GD

        if len(runs) == 1:
            return runs[0]
        labels = []
        for r in runs:
            size, where = "", ""
            ds = GD.open_ds(r["path"])
            if ds is not None:
                size = f"{ds.RasterXSize}×{ds.RasterYSize} px"
                gt = ds.GetGeoTransform()
                cx = gt[0] + gt[1] * ds.RasterXSize / 2
                cy = gt[3] + gt[5] * ds.RasterYSize / 2
                try:
                    # Runs of the same years differ only by an area digest, which tells a human
                    # nothing. Where it is on the planet tells them everything.
                    lo, la, _e, _n = GD.transform_bounds(GD.crs_string(ds), "EPSG:4326",
                                                         cx, cy, cx, cy)
                    where = f"  near {abs(la):.2f}°{'N' if la >= 0 else 'S'} "                             f"{abs(lo):.2f}°{'E' if lo >= 0 else 'W'}"
                except Exception:
                    where = ""
                ds = None
            labels.append(
                f"{r['year_a']} → {r['year_b']}{where}   {size}"
                + (f"   {r['cells']} embedding tiles" if r["cells"] else "   no embeddings"))
        pick, ok = QInputDialog.getItem(self, "Open which change map?",
                                        f"{len(runs)} saved runs in that folder:",
                                        labels, 0, False)
        if not ok:
            return None
        return runs[labels.index(pick)]

    def _open_existing(self):
        """Reconnect the dock to a run saved earlier.

        Everything downstream — threshold, Auto, Polygonize, Save, and the whole classifier —
        gates on `vrt_path` / `out_dir` / `layer_id`, and those were only ever set as a side
        effect of a job running in THIS session. So a saved result used to be openable as a
        picture and nothing more, even though the tiles, the VRT and the cell stores with the
        embeddings were all still sitting there intact.
        """
        from qgis.core import QgsRectangle
        from .engine import gdalio as GD

        folder = QFileDialog.getExistingDirectory(self, "Folder of a saved change map",
                                                  self._last_dir())
        if not folder:
            return
        self._remember_dir(folder)
        runs = self._find_runs(folder)
        if not runs:
            self.status.setText(
                "No saved change map in that folder. Pick the folder a previous run wrote to — "
                "it contains change_<from>_<to>.vrt.")
            return
        run = self._choose_run(runs)
        if run is None:
            self.status.setText("Open cancelled — nothing was changed.")
            return
        vrt_path, ya, yb = run["path"], run["year_a"], run["year_b"]

        ds = GD.open_ds(vrt_path)
        if ds is None:
            self.status.setText(f"Could not open {os.path.basename(vrt_path)}.")
            return
        gt = ds.GetGeoTransform()
        w, h = ds.RasterXSize, ds.RasterYSize
        src_crs = GD.crs_string(ds)
        xs = (gt[0], gt[0] + gt[1] * w)
        ys = (gt[3], gt[3] + gt[5] * h)
        ds = None

        self.out_dir = os.path.dirname(vrt_path)
        self.vrt_path = vrt_path
        from .engine import store as ST
        self.name_edit.blockSignals(True)
        # A saved name is the whole point of having typed one; only fall back to coordinates
        # when the run really has none.
        self.name_edit.setText(ST.load_meta(self.out_dir).get("name", "") or "")
        self.name_edit.blockSignals(False)
        self._sync_name_placeholder()
        # Deliberately NOT touching `Save to:`. Setting it here silently redirected the next
        # run into the folder just opened, and with a matching year pair that meant writing a
        # second area's tiles on top of the first and overwriting its VRT. Opening is a read;
        # where new work goes stays the user's choice.
        for combo, year in ((self.year_a, ya), (self.year_b, yb)):
            combo.blockSignals(True)
            combo.setCurrentText(str(year))
            combo.blockSignals(False)
        # The raster is the authority on its own pixel size; the tile filenames encode it too but
        # this cannot drift out of step with the file.
        res = abs(gt[1])
        for label, value in _DETAIL.items():
            if abs(value - res) < 1e-6:
                self.detail.blockSignals(True)
                self.detail.setCurrentText(label)
                self.detail.blockSignals(False)

        lo, la, hi, ha = GD.transform_bounds(src_crs, "EPSG:4326",
                                             min(xs), min(ys), max(xs), max(ys))
        self.bbox = (lo, la, hi, ha)
        self._release_layers(self._group_name())   # now that years and area are both known
        try:
            tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                        QgsProject.instance().crs(), QgsProject.instance())
            self.rect = tr.transformBoundingBox(QgsRectangle(lo, la, hi, ha))
            self._show_area_band(self.rect)
            # Go and look at it. Reopening a run and being left wherever the canvas happened to
            # be is the same as not having opened it.
            r = QgsRectangle(self.rect)
            r.scale(1.05)
            self.canvas.setExtent(r)
            self.canvas.refresh()
        except Exception:
            self.rect = None            # the outline is a nicety; never block the reopen for it

        self._refresh_layer()
        self._describe_area()
        n_cells = len([f for f in os.listdir(self.out_dir) if f.startswith("cells_")])
        note = (f", {n_cells} embedding tiles (classifier ready)." if n_cells else
                ". No embedding tiles here, so the classifier cannot run; re-run to capture them.")
        self._sync()          # the panel needs to be enabled before it can rebuild its layer
        self._register_run()
        restored = None
        if getattr(self, "classify", None) is not None:
            try:
                restored = self.classify.restore()
            except Exception as exc:
                restored = f"Could not restore polygons/labels: {exc}"
        self.status.setText(
            f"Opened {ya}→{yb} from {os.path.basename(self.out_dir)} — {w}×{h} px" + note
            + (f"  {restored}" if restored else ""))
        self._sync()

    def _python_exe(self):
        for c in (os.path.join(sys.exec_prefix, "python.exe"),
                  os.path.join(os.environ.get("PYTHONHOME", ""), "python.exe")):
            if c and os.path.isfile(c):
                return c
        return "python"

    def _engine_root(self):
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.isdir(os.path.join(here, "embed_cd")):
            return here                                  # vendored (zip install)
        return os.path.abspath(os.path.join(here, "..", ".."))   # dev mode

    def _run(self):
        if self.proc is not None or self.bbox is None:
            return
        ya, yb = int(self.year_a.currentText()), int(self.year_b.currentText())
        if ya == yb:
            self.status.setText("Pick two different years.")
            return

        e = self._estimate()
        # The bill scales with area AND Detail now that coarse jobs read overviews, so the one
        # way to start an hour-long job is a big rectangle at 10 m. Say so before it starts.
        if e["gb"] > 8 or e["poly_gb"] > 1.0:
            msg = (f"This area is ~{e['w']:.0f}×{e['h']:.0f} km:\n\n"
                   f"  • ~{e['tiles']} tiles, ~{e['gb']:.1f} GB to download\n"
                   f"  • roughly {e['minutes']:.0f} minutes\n"
                   f"  • output {e['out_px'] / 1e6:.0f} Mpx at {self.detail.currentText()}\n\n"
                   f"Memory during the job stays flat (~0.6 GB) however big the area is. "
                   f"But 'Make polygons' afterwards needs about {e['poly_gb']:.1f} GB at this "
                   f"Detail — a coarser Detail cuts that (it does NOT cut the download).\n\n"
                   f"Go ahead?")
            yes = _scoped(QMessageBox, "StandardButton", "Yes")
            if QMessageBox.question(self, "Large area", msg) != yes:
                return

        chosen = self.out_edit.text().strip()
        # The AREA is part of the folder name, not just the years. Keyed on years alone, two
        # different areas compared over the same period land in one folder: tiles interleave,
        # `CellIndex` picks up both, and the second run's VRT overwrites the first. That is not
        # hypothetical — it happened, mixing a Vancouver Island run into a Mt Bishop one.
        # Identical bbox still gives an identical key, so resuming a cancelled job still works.
        run = f"change_{ya}_{yb}_{self._area_key()}"
        if chosen:
            self.out_dir = os.path.join(chosen, run)
        else:
            self._tmp_root = self._tmp_root or tempfile.mkdtemp(prefix="embed_cd_")
            self.out_dir = os.path.join(self._tmp_root, run)
        os.makedirs(self.out_dir, exist_ok=True)
        self._save_meta()
        cancel_flag = os.path.join(self.out_dir, ".cancel")
        if os.path.exists(cancel_flag):
            os.remove(cancel_flag)

        spec = {
            "bbox": list(self.bbox), "year_a": ya, "year_b": yb,
            "out_dir": self.out_dir, "dst_crs": self._target_crs(),
            "res_m": _DETAIL[self.detail.currentText()],
            "cache_dir": self._cache_dir(), "name": f"change_{ya}_{yb}",
            # Pool the embeddings while the tiles are briefly in memory. Always on: it costs
            # under 1% of job time, and it is the only chance to capture them at all.
            # Sized in GROUND metres, so it means the same thing at every Detail.
            "cell_m": _CELL_M,
        }
        root = self._engine_root()
        self._release_layers(self._group_name())
        self.vrt_path = None
        self._canceled = False
        self.proc = QProcess(self)
        # Start from the REAL environment — processEnvironment() is empty unless previously
        # set, and handing the child only PYTHONPATH would strip PATH and break python startup.
        from qgis.PyQt.QtCore import QProcessEnvironment
        env = QProcessEnvironment.systemEnvironment()
        parts = [root]
        import site as _site
        user_site = _site.getusersitepackages()
        if isinstance(user_site, str) and os.path.isdir(user_site):
            parts.append(user_site)        # where a non-admin `pip install` actually puts things
        for p in sys.path:                 # carry QGIS's own resolved paths across
            if p and os.path.isdir(p) and p not in parts:
                parts.append(p)
        existing = env.value("PYTHONPATH", "")
        if existing:
            parts.append(existing)
        env.insert("PYTHONPATH", os.pathsep.join(parts))
        env.remove("PYTHONNOUSERSITE")     # QGIS sets this; it would hide the user-site deps
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.status.setText("Checking coverage…")
        self._sync()
        self.proc.start(self._python_exe(),
                        ["-m", "embed_cd.worker", json.dumps(spec)])

    def _cancel(self):
        if self.proc is None:
            return
        self._canceled = True
        if self.out_dir:
            open(os.path.join(self.out_dir, ".cancel"), "w").close()   # stop after this tile
        self.status.setText("Finishing the current tile, then stopping…")

    def _on_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in text.splitlines():
            parts = line.split()
            if line.startswith("PLAN ") and len(parts) >= 5:
                self.n_tiles = int(parts[1])
                gb = float(parts[2]) / 1e9
                n_partial = int(parts[5]) if len(parts) >= 6 else 0
                self.progress.setRange(0, max(1, self.n_tiles))
                self.progress.setValue(0)
                msg = (f"{self.n_tiles} tiles · ~{gb:.1f} GB to read (finished tiles are "
                       f"reused if you re-run) · output {parts[3]}×{parts[4]} px")
                if n_partial:
                    msg += (f"\n{n_partial} tile(s) have only one of the two years — those "
                            "areas are mapped as 'no data'; the rest still gets a change map.")
                self.status.setText(msg)
            elif line.startswith("TILE ") and len(parts) >= 4:
                done, total = int(parts[1]), int(parts[2])
                self.vrt_path = " ".join(parts[3:])
                self.progress.setRange(0, max(1, total))
                self.progress.setValue(done)
                self.status.setText(f"{done} of {total} tiles")
                self._refresh_layer()
                if done == 1:
                    self._register_run()
            elif line.startswith("AUTO ") and len(parts) >= 3:
                t, frac = float(parts[1]), float(parts[2])
                self.slider.setValue(int(round(t * 100)))
                self.status.setText(f"Done. Auto cutoff {t:.2f} — {frac*100:.1f}% of the "
                                    "area changed.")
            elif line.startswith("ERR "):
                self.status.setText(line[4:])

    def _on_finished(self, exit_code, _status):
        self._on_output()
        self.proc.deleteLater()
        self.proc = None
        self.progress.setVisible(False)
        if self._canceled:
            self.status.setText(self.status.text() + "  (cancelled — rerun to resume)")
        elif exit_code != 0 and self.layer_id is None:
            self.iface.messageBar().pushMessage(
                "EMBED-CD", self.status.text() or "The job failed.",
                level=_scoped(Qgis, "MessageLevel", "Warning"), duration=6)
        self._sync()

    # ---------------- layer ----------------
    def _refresh_layer(self):
        """Two layers, always: the change map on top, and a thematic COVERAGE layer under it.

        Without the coverage layer, 'no change' and 'no data' both render as nothing, so you
        cannot tell whether an empty area was surveyed and stable or never looked at. That
        makes the whole map untrustworthy, so the coverage layer is not optional.
        """
        if not self.vrt_path or not os.path.exists(self.vrt_path):
            return
        a, b = self.year_a.currentText(), self.year_b.currentText()

        cov = QgsProject.instance().mapLayer(self.cov_layer_id) if self.cov_layer_id else None
        if cov is None:
            cov = QgsRasterLayer(self.vrt_path, f"data coverage {a}→{b}")
            if cov.isValid():
                self._style_coverage(cov, a, b)
                self._add_to_group(cov, bottom=True)     # underneath the change map
                self.cov_layer_id = cov.id()
        elif cov.source() != self.vrt_path:
            # Re-point, don't reload. reloadData() and setDataSource() to the SAME path both
            # leave the layer showing the dataset it first opened — measured — which is why
            # tiles used to appear only after a zoom. The worker hands us a new path per tile
            # precisely so this call has something new to open.
            cov.setDataSource(self.vrt_path, cov.name(), "gdal")
            self._style_coverage(cov, a, b)
            cov.triggerRepaint()

        layer = QgsProject.instance().mapLayer(self.layer_id) if self.layer_id else None
        if layer is None:
            layer = QgsRasterLayer(self.vrt_path, f"change {a}→{b}")
            if not layer.isValid():
                return
            self._style(layer)
            self._add_to_group(layer)
            self.layer_id = layer.id()
            self._clear_area_band()      # the raster now shows the extent; outline is clutter
            self._sync()
        elif layer.source() != self.vrt_path:
            layer.setDataSource(self.vrt_path, layer.name(), "gdal")
            self._style(layer)               # also re-applies the current threshold
            layer.triggerRepaint()

    def _style_coverage(self, layer, year_a, year_b):
        """Band 2 as a thematic map of WHY a pixel has no answer. 'data in both years' is
        transparent so it doesn't hide the change map — everything you can SEE here is a
        place the change map has no opinion about."""
        from qgis.core import QgsPalettedRasterRenderer
        # Four distinguishable hues, not two greys: these classes are the difference between
        # "nothing changed" and "we couldn't tell", so they have to be told apart at a glance.
        # Nothing here uses orange or yellow — those belong to the change ramp on top.
        # Class 0 is kept deliberately FAINT. It marks the edge of the mapped area, which is
        # the least actionable thing here and usually the largest — at a high alpha it washes
        # out the basemap and the change map you actually came to look at. The genuinely
        # informative classes (a year missing) stay bold because they are rare and matter.
        classes = [
            (0, QColor(45, 50, 60, 60), "outside the mapped area"),
            (1, QColor(0, 0, 0, 0), "data in both years"),
            (2, QColor(214, 60, 200, 220), f"no data in {year_a}"),
            (3, QColor(60, 170, 235, 220), f"no data in {year_b}"),
            (4, QColor(255, 255, 255, 230), "no data in either year"),
        ]
        entries = [QgsPalettedRasterRenderer.Class(v, c, lbl) for v, c, lbl in classes]
        layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 2, entries))
        layer.triggerRepaint()

    def _style(self, layer):
        ramp = QgsColorRampShader()
        ramp.setColorRampType(_scoped(QgsColorRampShader, "Type", "Interpolated"))
        # Cyan -> yellow -> red. The previous pale brown vanished against the greens and
        # greys of satellite imagery, which is the only thing this is ever drawn on top of.
        ramp.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(0.0, QColor(0, 210, 235), "least changed"),
            QgsColorRampShader.ColorRampItem(0.4, QColor(255, 225, 40), ""),
            QgsColorRampShader.ColorRampItem(0.8, QColor(235, 30, 30), "most changed"),
        ])
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(ramp)
        r = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        r.setClassificationMin(0.0)
        r.setClassificationMax(0.8)
        layer.setRenderer(r)
        layer.setOpacity(0.85)
        self._apply_threshold(layer)

    def _threshold(self):
        return self.slider.value() / 100.0

    def _on_threshold(self):
        self.thr_lbl.setText(f"{self._threshold():.2f}")
        self._thr_timer.start()

    def _apply_threshold(self, layer=None):
        layer = layer or (QgsProject.instance().mapLayer(self.layer_id)
                          if self.layer_id else None)
        if layer is None:
            return
        px = QgsRasterTransparency.TransparentSingleValuePixel()
        px.min = -2.0                      # also hides the -1 nodata
        px.max = self._threshold()
        px.percentTransparent = 100.0
        tr = QgsRasterTransparency()
        tr.setTransparentSingleValuePixelList([px])
        layer.renderer().setRasterTransparency(tr)
        layer.triggerRepaint()

    def _auto(self):
        """Recompute Otsu from the finished mosaic (the run reports it too)."""
        if not self.vrt_path:
            return
        try:
            import numpy as np
            sys.path.insert(0, self._engine_root())
            from .engine import score as S, gdalio as GD
            hist = np.zeros(S.HIST_BINS, dtype=np.int64)
            ds = GD.open_ds(self.vrt_path)
            band = ds.GetRasterBand(1)
            w, h = ds.RasterXSize, ds.RasterYSize
            step = max(1, 4_000_000 // max(1, w))      # ~4M pixels a time, never the whole mosaic
            for y in range(0, h, step):
                hist += S.histogram(band.ReadAsArray(0, y, w, min(step, h - y)))
            t = S.otsu_from_histogram(hist)
            self.slider.setValue(int(round(t * 100)))
            self.status.setText(f"Auto cutoff {t:.2f} — "
                                f"{S.fraction_above(hist, t)*100:.1f}% of the area changed.")
        except Exception as exc:
            self.status.setText(f"Auto failed: {exc}")

    # ---------------- ground truth photos ----------------
    def _sync_photos(self):
        """A year EOX does not publish says so in its tooltip rather than quietly showing a
        neighbouring year under the wrong label — the strip exists to check what really happened,
        so the label has to be trustworthy. No area needed: these are streamed global tiles."""
        from .engine import basemap as BM
        for year, btn in self.photo_btns.items():
            got = BM.nearest_year(year)
            btn.setToolTip(
                f"Sentinel-2 cloudless {year}." if got == year else
                f"No {year} mosaic exists — this button shows {got} instead.")

    def _toggle_photo(self, year):
        """Independent, NOT exclusive. Comparing before and after means having both loaded and
        flicking the top one's visibility in the layer panel — an exclusive strip makes exactly
        that impossible."""
        from .engine import basemap as BM

        got = BM.nearest_year(year)
        if not self.photo_btns[year].isChecked():
            self._remove_photo(year)
            return
        layer = QgsRasterLayer(BM.xyz_uri(got), BM.layer_name(got), "wms")
        if not layer.isValid():
            self.photo_btns[year].setChecked(False)
            self.status.setText(f"Could not load {got} imagery (network?).")
            return
        layer.setAttribution(BM.ATTRIBUTION)
        QgsProject.instance().addMapLayer(layer, False)
        # Bottom of the tree: the photo is a backdrop to check the change map against, never a
        # replacement for it.
        QgsProject.instance().layerTreeRoot().insertLayer(-1, layer)
        self.photo_ids[year] = layer.id()
        if got != year:
            self.status.setText(f"No {year} mosaic — showing {got}.")

    def _remove_photo(self, year=None):
        """One year, or all of them."""
        years = list(self.photo_ids) if year is None else [year]
        for y in years:
            lid = self.photo_ids.pop(y, None)
            if lid:
                QgsProject.instance().removeMapLayer(lid)
            btn = self.photo_btns.get(y)
            if btn is not None:
                btn.setChecked(False)

    # ---------------- export ----------------
    def _polygonize(self):
        """Plain polygons of the changed area, no embeddings — the classifier group does the
        richer version. Uses the same engine call, so there is one polygonizer, not two."""
        if not self.vrt_path:
            return
        try:
            from .engine import objects as OB
            from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
            polys, crs = OB.polygonize(self.vrt_path, self._threshold(), min_area_ha=0.01)
            if not polys:
                self.status.setText("Nothing above the current cutoff.")
                return
            layer = QgsVectorLayer(f"Polygon?crs={crs}", "changed (polygons)", "memory")
            pr = layer.dataProvider()
            pr.addAttributes([QgsField("change", _qvariant_string()),
                              QgsField("area_ha", _qvariant_double())])
            layer.updateFields()
            tag = f"{self.year_a.currentText()}->{self.year_b.currentText()}"
            feats = []
            for poly in polys:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromWkt(poly["wkt"]))
                f.setAttributes([tag, float(poly["area_ha"])])
                feats.append(f)
            pr.addFeatures(feats)
            layer.updateExtents()
            QgsProject.instance().addMapLayer(layer)
            self.status.setText(f"{len(polys)} polygons at cutoff {self._threshold():.2f}.")
        except Exception as exc:
            self.status.setText(f"Polygonize failed: {exc}")

    def _save_geotiff(self):
        if not self.vrt_path:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save change map",
            f"change_{self.year_a.currentText()}_{self.year_b.currentText()}.tif",
            "GeoTIFF (*.tif)")
        if not path:
            return
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            # Translate streams the VRT block by block itself, so this never holds the mosaic
            gdal.Translate(path, self.vrt_path, creationOptions=[
                "COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
            self.iface.messageBar().pushMessage(
                "EMBED-CD", f"Saved {path}",
                level=_scoped(Qgis, "MessageLevel", "Success"), duration=5)
        except Exception as exc:
            self.status.setText(f"Save failed: {exc}")

    # ---------------- helpers ----------------
    def _target_crs(self):
        a = QgsProject.instance().crs().authid()
        return a if a.startswith("EPSG:") else "EPSG:3857"

    def _cache_dir(self):
        from qgis.core import QgsApplication
        return os.path.join(QgsApplication.qgisSettingsDirPath(), "cache", "embed_cd")

    def _release_layers(self, new_group):
        """Replace this area's layers, or leave another area's alone.

        Re-running the SAME area should replace its layers; starting a different one should
        not touch what is already there. Previously the raster was removed either way while the
        polygons were not, so a new area silently half-erased the old one — a complete group
        left behind is the honest version of "you now have two areas open".
        """
        # Compare the AREA, not the name. Two different areas left with the same name produce
        # the same group name, so name-matching read them as one run and merged their layers —
        # which is exactly the case someone hits by drawing a second area and not retyping.
        key = self._area_key() if self.bbox is not None else None
        same_area = self._current_area_key is not None and key == self._current_area_key
        if same_area:
            self._remove_layer()                          # re-run: replace this area's layers
            if getattr(self, "classify", None) is not None:
                self.classify.detach()
            if not self._current_group or new_group != self._current_base:
                self._current_group = self._resolve_group(new_group)   # renamed in place
        else:
            if self._current_area_key is not None:
                self.layer_id = self.cov_layer_id = None  # another area: detach, do not remove
            else:
                self._remove_layer()
            if getattr(self, "classify", None) is not None:
                self.classify.detach()
            self._current_group = self._resolve_group(new_group)
        self._current_base = new_group
        self._current_area_key = key

    def _remove_layer(self):
        for attr in ("layer_id", "cov_layer_id"):
            lid = getattr(self, attr, None)
            if lid:
                QgsProject.instance().removeMapLayer(lid)
                setattr(self, attr, None)

    def cleanup(self):
        if self.proc is not None:
            self._cancel()
            self.proc.kill()
        if getattr(self, "classify", None) is not None:
            self.classify.cleanup()
        self._remove_layer()
        self._remove_photo()          # all years
        self._clear_area_band()
        if self.tool is not None:
            self.tool.clear()
            self.canvas.unsetMapTool(self.tool)
            self.tool = None
        if self._tmp_root and os.path.isdir(self._tmp_root):
            shutil.rmtree(self._tmp_root, ignore_errors=True)
