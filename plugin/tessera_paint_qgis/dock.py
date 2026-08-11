"""TESSERA Paint dock.

ONE choice drives the UI: "Map land cover (one year)" or "Map change (between two years)".
That mode decides which year pickers are shown, whether the change panel is relevant, and
what the classifier learns from — so the user never picks years twice or has to know that
"features" and "mode" are the same decision. Steps: 1 area & data -> (2 change map) ->
3 paint classes -> 4 classify -> 5 export. Missing years are downloaded on demand.

Region is pinned once; loading another year re-fetches the SAME footprint, so years stay
pixel-aligned (needed for change mode) and switching years is a swap, not a re-frame. Up to
two years are held in memory. All math is in the `tessera_paint` engine; the fetch runs in
`tessera_paint/worker.py` via QProcess (PROJ-safe, UI responsive).
"""
import json
import os
import sys
import tempfile
from urllib.parse import quote

import numpy as np
import rasterio
import rasterio.transform

from qgis.PyQt.QtCore import Qt, QProcess, QTimer
from qgis.PyQt.QtGui import QColor, QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QLabel, QSlider, QFileDialog, QInputDialog, QListWidget, QListWidgetItem,
    QProgressBar, QButtonGroup, QGroupBox, QScrollArea, QSpinBox,
)
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsPointXY, QgsApplication, Qgis,
    QgsGeometry, QgsRectangle, QgsColorRampShader, QgsRasterShader,
    QgsSingleBandPseudoColorRenderer, QgsRasterTransparency, QgsField,
    QgsPalettedRasterRenderer,
)
from qgis.gui import QgsRubberBand

from .maptool import BrushTool, PlaceTool

try:
    from .tessera_paint import prepare, score, mask as make_mask, pca_rgb, budget, head, change
except ImportError:
    from tessera_paint import prepare, score, mask as make_mask, pca_rgb, budget, head, change

try:
    from qgis.gui import QgsCollapsibleGroupBox as _GroupBox
except ImportError:
    _GroupBox = QGroupBox

_YEARS = [str(y) for y in range(2017, 2026)]
_DEFAULT_YEAR = "2024"
_TILE_BACKSTOP = 400   # absurd-input safety only; the memory budget is the real gate
_YEAR_CAP = 2          # change needs a pair; znorm is kept only for the viewing year
_EOX_YEARS = [2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
_CLASS_COLORS = [
    (31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40),
    (148, 103, 189), (140, 86, 75), (227, 119, 194), (23, 190, 207),
]


def _scoped(owner, category, name):
    """Enum that works on both PyQt5 (QGIS 3) and PyQt6 (QGIS 4)."""
    try:
        return getattr(getattr(owner, category), name)
    except AttributeError:
        return getattr(owner, name)


def _qvariant_string():
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QMetaType.Type.QString
    except (ImportError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        return QVariant.String


class TesseraDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("TESSERA Paint", iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()

        # region / year state
        self.region = None            # pinned bbox (min_lon,min_lat,max_lon,max_lat) in EPSG:4326
        self.years = {}               # year:int -> {mosaic, stats, transform, crs}
        self.viewing = None           # int year currently active
        # classes: {name, color, strokes:[{negative, vecs[n,128], band}], threshold, sim}
        self.classes = []
        self.click_mode = "include"   # or "exclude"
        # ui/process state
        self.tool = None
        self.place_tool = None
        self.proc = None
        self.sim_layer_id = None
        self._sim_layer_key = None    # (id(class), sim.shape) the live layer was built for
        self.pca_layer_id = None
        self.region_band = None
        self.tmpdir = tempfile.mkdtemp(prefix="tessera_paint_")
        self.sim_path = os.path.join(self.tmpdir, "similarity.tif")
        self.pca_path = os.path.join(self.tmpdir, "embedding_pca.tif")
        self.mosaic_base = os.path.join(self.tmpdir, "mosaic")   # -> .npy + .json sidecar
        self.class_path = os.path.join(self.tmpdir, "classified.tif")
        # multi-class result (all classes competing)
        self.class_labels = None      # int16 [H,W], -1 = nodata
        self.class_margin = None      # float32 [H,W] in [0,1]
        self.class_names = []
        self.class_colors = []
        self.class_layer_id = None
        self._class_layer_key = None
        # change map between two loaded years: {a, b, score, feat, valid}
        self.change = None
        self._pending_change = None   # (yearA, yearB) waiting on fetches
        self.change_path = os.path.join(self.tmpdir, "change.tif")
        self.change_layer_id = None
        self.coverage_layer_id = None
        self._change_layer_key = None
        # the trained/loaded portable head (persists across area changes)
        self.head = None
        self.head_names = []
        self.head_colors = []
        self.head_feature = "raw"     # which feature space the current head was trained on
        # debounce for live thresholding: re-render at most ~every 60 ms while dragging
        self._thr_timer = QTimer(self)
        self._thr_timer.setSingleShot(True)
        self._thr_timer.setInterval(60)
        self._thr_timer.timeout.connect(self._apply_threshold)
        self._conf_timer = QTimer(self)
        self._conf_timer.setSingleShot(True)
        self._conf_timer.setInterval(60)
        self._conf_timer.timeout.connect(self._render_classified)
        self._chg_timer = QTimer(self)
        self._chg_timer.setSingleShot(True)
        self._chg_timer.setInterval(60)
        self._chg_timer.timeout.connect(self._apply_chg_threshold)

        self._build_ui()
        self._sync()

    # ================= UI construction =================
    def _build_ui(self):
        outer = QWidget()
        ol = QVBoxLayout(outer)

        # ---- 1 · Area & data ----
        g1 = _GroupBox("1 · Area and data")
        l1 = QVBoxLayout(g1)

        # The first question, because it drives everything else: one year, or two?
        moderow = QHBoxLayout()
        moderow.addWidget(QLabel("Map:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Land cover (one year)", "Change (between two years)"])
        self.mode_combo.setToolTip(
            "Land cover: classify what things ARE in a single year.\n"
            "Change: compare two years — where and how the surface changed.")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        moderow.addWidget(self.mode_combo, 1)
        l1.addLayout(moderow)

        pinrow = QHBoxLayout()
        self.place_btn = QPushButton("Place area (click map)")
        self.place_btn.setCheckable(True)
        self.place_btn.setToolTip("Then click the map: an area box auto-sized to fit your "
                                  "memory budget is pinned there.")
        self.place_btn.clicked.connect(self._toggle_place)
        pinrow.addWidget(self.place_btn)
        self.pin_btn = QPushButton("Use current view")
        self.pin_btn.setToolTip("Use the current map extent as the working area. Rejected if it "
                                "exceeds the memory budget — zoom in or use Place area.")
        self.pin_btn.clicked.connect(self._pin_region)
        pinrow.addWidget(self.pin_btn)
        l1.addLayout(pinrow)

        self.region_lbl = QLabel()
        self.region_lbl.setWordWrap(True)
        l1.addWidget(self.region_lbl)

        # --- single-year controls ---
        self.single_box = QWidget()
        sv = QHBoxLayout(self.single_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(QLabel("Year:"))
        self.year_combo = QComboBox()
        self.year_combo.addItems(_YEARS)
        self.year_combo.setCurrentText(_DEFAULT_YEAR)
        sv.addWidget(self.year_combo)
        self.load_btn = QPushButton("Get data")
        self.load_btn.setToolTip("Download this year's embeddings for the area.")
        self.load_btn.clicked.connect(self._on_load)
        sv.addWidget(self.load_btn)
        l1.addWidget(self.single_box)

        # --- change controls (same row position; only one is ever visible) ---
        self.change_box = QWidget()
        cv = QVBoxLayout(self.change_box)
        cv.setContentsMargins(0, 0, 0, 0)
        yrow = QHBoxLayout()
        yrow.addWidget(QLabel("From:"))
        self.chg_a = QComboBox()
        yrow.addWidget(self.chg_a)
        yrow.addWidget(QLabel("To:"))
        self.chg_b = QComboBox()
        yrow.addWidget(self.chg_b)
        self.chg_btn = QPushButton("Get data & compare")
        self.chg_btn.setToolTip("Downloads whichever years aren't already in memory, then maps "
                                "how much each pixel's year-long signature changed. No labels "
                                "needed.")
        self.chg_btn.clicked.connect(self._make_change)
        yrow.addWidget(self.chg_btn)
        cv.addLayout(yrow)
        l1.addWidget(self.change_box)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        l1.addWidget(self.progress)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_load)
        self.cancel_btn.setVisible(False)
        l1.addWidget(self.cancel_btn)

        advrow = QHBoxLayout()
        advrow.addWidget(QLabel("Detail:"))
        self.detail_combo = QComboBox()
        self._detail = {"10 m (full)": 1, "20 m": 2, "50 m": 5, "100 m": 10}
        self.detail_combo.addItems(list(self._detail))
        self.detail_combo.setToolTip(
            "Output pixel size. 10 m = full resolution (best for shorelines, small areas). "
            "Coarser lets a bigger area fit in memory.")
        advrow.addWidget(self.detail_combo)
        advrow.addWidget(QLabel("Memory:"))
        self.mem_spin = QSpinBox()
        self.mem_spin.setRange(10, 90)
        self.mem_spin.setValue(50)
        self.mem_spin.setSuffix(" %")
        self.mem_spin.setToolTip("Share of currently-free system RAM this tool may use — sets "
                                 "the largest area you can load.")
        self.mem_spin.valueChanged.connect(lambda _=0: self._update_budget_label())
        advrow.addWidget(self.mem_spin)
        self.budget_lbl = QLabel()
        advrow.addWidget(self.budget_lbl)
        advrow.addStretch(1)
        l1.addLayout(advrow)

        refrow = QHBoxLayout()
        refrow.addWidget(QLabel("Show:"))
        self.basemap_btn = QPushButton("Sentinel-2 photo")
        self.basemap_btn.setToolTip("Add EOX Sentinel-2 cloudless imagery for the selected year "
                                    "— real imagery matched to the embedding period.")
        self.basemap_btn.clicked.connect(self._add_basemap)
        refrow.addWidget(self.basemap_btn)
        self.pca_btn = QPushButton("False color (data)")
        self.pca_btn.setToolTip("False-color view of the embeddings themselves: similar "
                                "signatures share a color. This is what the tool sees.")
        self.pca_btn.clicked.connect(self._show_pca)
        refrow.addWidget(self.pca_btn)
        l1.addLayout(refrow)
        ol.addWidget(g1)

        # ---- 2 · Change result (only meaningful in change mode) ----
        self.change_result_box = _GroupBox("2 · Change map")
        lC = QVBoxLayout(self.change_result_box)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Changed if ≥"))
        self.chg_slider = QSlider(_scoped(Qt, "Orientation", "Horizontal"))
        self.chg_slider.setRange(0, 100)
        self.chg_slider.setValue(20)
        self.chg_slider.valueChanged.connect(self._on_chg_threshold)
        crow.addWidget(self.chg_slider)
        self.chg_lbl = QLabel("0.20")
        crow.addWidget(self.chg_lbl)
        self.chg_auto_btn = QPushButton("Auto")
        self.chg_auto_btn.setToolTip("Pick the cutoff automatically (Otsu): the value that best "
                                     "splits this scene into changed and unchanged. Adapts to "
                                     "the scene instead of guessing a fixed number.")
        self.chg_auto_btn.clicked.connect(self._auto_threshold)
        crow.addWidget(self.chg_auto_btn)
        lC.addLayout(crow)
        crow2 = QHBoxLayout()
        self.chg_poly_btn = QPushButton("Polygonize")
        self.chg_poly_btn.setToolTip("Turn the thresholded change into editable polygons.")
        self.chg_poly_btn.clicked.connect(self._polygonize_change)
        crow2.addWidget(self.chg_poly_btn)
        self.chg_save_btn = QPushButton("Save change…")
        self.chg_save_btn.clicked.connect(self._save_change)
        crow2.addWidget(self.chg_save_btn)
        lC.addLayout(crow2)
        self.chg_hint = QLabel("Optional: paint the kinds of change below (clearcut, regrowth, "
                               "flooding…) and Classify to label them.")
        self.chg_hint.setWordWrap(True)
        lC.addWidget(self.chg_hint)
        ol.addWidget(self.change_result_box)

        # ---- 3 · Classes ----
        g2 = _GroupBox("3 · Classes")
        l2 = QVBoxLayout(g2)
        self.class_list = QListWidget()
        self.class_list.setToolTip("Each class has its own painted pixels and threshold. Select "
                                   "one to make it active; new paint goes to it.")
        self.class_list.currentRowChanged.connect(self._on_class_changed)
        self.class_list.setMaximumHeight(130)
        l2.addWidget(self.class_list)

        crow3 = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._add_class)
        crow3.addWidget(self.add_btn)
        self.rename_btn = QPushButton("Rename")
        self.rename_btn.clicked.connect(self._rename_class)
        crow3.addWidget(self.rename_btn)
        self.del_btn = QPushButton("Delete")
        self.del_btn.clicked.connect(self._delete_class)
        crow3.addWidget(self.del_btn)
        l2.addLayout(crow3)

        moderow2 = QHBoxLayout()
        moderow2.addWidget(QLabel("Paint:"))
        self.include_btn = QPushButton("Include")
        self.include_btn.setCheckable(True)
        self.include_btn.setChecked(True)
        self.include_btn.setToolTip("Left-drag paints INCLUDE pixels for the active class.")
        self.exclude_btn = QPushButton("Exclude")
        self.exclude_btn.setCheckable(True)
        self.exclude_btn.setToolTip("Left-drag paints EXCLUDE pixels (things that are NOT this "
                                    "class). Right-drag always excludes. Excludes from all "
                                    "classes become a shared 'other' when you Classify.")
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self.include_btn)
        grp.addButton(self.exclude_btn)
        self.include_btn.clicked.connect(lambda: self._set_mode("include"))
        self.exclude_btn.clicked.connect(lambda: self._set_mode("exclude"))
        moderow2.addWidget(self.include_btn)
        moderow2.addWidget(self.exclude_btn)
        moderow2.addWidget(QLabel("Brush:"))
        self.brush_spin = QSpinBox()
        self.brush_spin.setRange(0, 25)
        self.brush_spin.setValue(3)
        self.brush_spin.setSuffix(" px")
        self.brush_spin.setToolTip("Brush radius in raster pixels. Drag across the map to paint "
                                   "training pixels — one stroke gives hundreds of samples. "
                                   "0 = single pixel.")
        moderow2.addWidget(self.brush_spin)
        l2.addLayout(moderow2)

        srow = QHBoxLayout()
        self.undo_btn = QPushButton("Undo last")
        self.undo_btn.clicked.connect(self._undo_seed)
        srow.addWidget(self.undo_btn)
        self.clear_btn = QPushButton("Clear paint")
        self.clear_btn.clicked.connect(self._clear_seeds)
        srow.addWidget(self.clear_btn)
        l2.addLayout(srow)

        trow = QHBoxLayout()
        trow.addWidget(QLabel("Preview threshold:"))
        self.slider = QSlider(_scoped(Qt, "Orientation", "Horizontal"))
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.valueChanged.connect(self._on_threshold_label)
        self.slider.sliderReleased.connect(self._apply_threshold)
        trow.addWidget(self.slider)
        self.thr_label = QLabel("0.50")
        trow.addWidget(self.thr_label)
        l2.addLayout(trow)
        ol.addWidget(g2)

        # ---- 4 · Classify ----
        g3 = _GroupBox("4 · Classify")
        l3 = QVBoxLayout(g3)
        self.classify_btn = QPushButton("Classify all classes")
        self.classify_btn.setToolTip(
            "Train one model on every class's painted pixels and let them COMPETE: each pixel "
            "gets exactly one label, so classes can't overlap. Needs 2+ painted classes.")
        self.classify_btn.clicked.connect(self._classify)
        l3.addWidget(self.classify_btn)

        headrow = QHBoxLayout()
        self.apply_head_btn = QPushButton("Apply to this area")
        self.apply_head_btn.setToolTip("Re-run the trained classifier on the currently loaded "
                                       "area — no retraining. Move to a new area, get data, "
                                       "then click this.")
        self.apply_head_btn.clicked.connect(self._apply_head)
        headrow.addWidget(self.apply_head_btn)
        self.save_head_btn = QPushButton("Save…")
        self.save_head_btn.setToolTip("Save the classifier to reuse in another session.")
        self.save_head_btn.clicked.connect(self._save_head)
        headrow.addWidget(self.save_head_btn)
        self.load_head_btn = QPushButton("Load…")
        self.load_head_btn.setToolTip("Load a saved classifier and apply it to this area.")
        self.load_head_btn.clicked.connect(self._load_head)
        headrow.addWidget(self.load_head_btn)
        l3.addLayout(headrow)

        crow4 = QHBoxLayout()
        crow4.addWidget(QLabel("Min confidence:"))
        self.conf_slider = QSlider(_scoped(Qt, "Orientation", "Horizontal"))
        self.conf_slider.setRange(0, 100)
        self.conf_slider.setValue(0)
        self.conf_slider.setToolTip("How decisively the winning class must beat the runner-up. "
                                    "Raise it to leave ambiguous pixels unclassified.")
        self.conf_slider.valueChanged.connect(self._on_conf_changed)
        crow4.addWidget(self.conf_slider)
        self.conf_label = QLabel("0.00")
        crow4.addWidget(self.conf_label)
        l3.addLayout(crow4)
        ol.addWidget(g3)

        # ---- 5 · Export ----
        g4e = _GroupBox("5 · Export")
        l4e = QVBoxLayout(g4e)
        erow = QHBoxLayout()
        self.mask_btn = QPushButton("Save mask…")
        self.mask_btn.setToolTip("Save the ACTIVE class's thresholded preview as a mask.")
        self.mask_btn.clicked.connect(self._create_mask)
        erow.addWidget(self.mask_btn)
        self.poly_btn = QPushButton("Save polygons…")
        self.poly_btn.clicked.connect(self._polygonize)
        erow.addWidget(self.poly_btn)
        l4e.addLayout(erow)
        erow2 = QHBoxLayout()
        self.save_class_btn = QPushButton("Save classified…")
        self.save_class_btn.setToolTip("Save the multi-class result (raster + polygons with "
                                       "class names).")
        self.save_class_btn.clicked.connect(self._save_classified)
        erow2.addWidget(self.save_class_btn)
        l4e.addLayout(erow2)
        ol.addWidget(g4e)
        if hasattr(g4e, "setCollapsed"):
            g4e.setCollapsed(True)

        # ---- How it works (collapsed) ----
        g4 = _GroupBox("How it works")
        l4 = QVBoxLayout(g4)
        info = QLabel(
            "Each pixel carries a 128-d TESSERA embedding summarizing a whole year of "
            "Sentinel-1 + Sentinel-2. Classes separate by year-long behavior (irrigated vs "
            "rain-fed, seasonal vs permanent water) — things a single photo can't tell apart. "
            "In Change mode the classifier learns from baseline + difference, so classes are "
            "kinds of change. Painted pixels are embeddings, so a trained classifier carries "
            "to other areas and years."
        )
        info.setWordWrap(True)
        l4.addWidget(info)
        ol.addWidget(g4)
        if hasattr(g4, "setCollapsed"):
            g4.setCollapsed(True)

        ol.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(outer)
        self.setWidget(scroll)

        self.detail_combo.currentIndexChanged.connect(lambda _=0: self._sync())
        self._update_budget_label()
        self._on_mode_changed()

    # ================= small helpers =================
    def _yr(self):
        return self.years.get(self.viewing)

    def _detail_factor(self):
        return self._detail[self.detail_combo.currentText()]

    def _available_bytes(self):
        try:
            import psutil
            return int(psutil.virtual_memory().available)
        except Exception:
            pass
        try:  # Windows without psutil
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                            ("ullTotalPageFile", ctypes.c_uint64),
                            ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64),
                            ("ullAvailVirtual", ctypes.c_uint64),
                            ("ullAvailExtendedVirtual", ctypes.c_uint64)]

            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullAvailPhys)
        except Exception:
            return 4 * 1024 ** 3

    def _budget_bytes(self):
        return int(self._available_bytes() * self.mem_spin.value() / 100.0)

    def _update_budget_label(self):
        b = self._budget_bytes()
        maxkm = budget.max_box_side_m(b) / 1000.0
        self.budget_lbl.setText(f"≈{b / 1e9:.1f} GB free → max ~{maxkm:.0f} km/side")

    def _active_class(self):
        i = self.class_list.currentRow()
        return self.classes[i] if 0 <= i < len(self.classes) else None

    def _color_icon(self, color):
        pix = QPixmap(14, 14)
        pix.fill(color)
        return QIcon(pix)

    def _sync(self):
        """Refresh status text + enabled/disabled state from current model."""
        loading = self.proc is not None
        loaded = self._yr() is not None
        chg_mode = self._mode() == "change"
        # in change mode the classifier needs the change map, not just a loaded year
        ready = (self.change is not None) if chg_mode else loaded
        cls = self._active_class()
        has_sim = cls is not None and cls["sim"] is not None

        # area / inventory status — always answers "what do I have, what's next?"
        if self.region is None:
            self.region_lbl.setText("<b>Step 1:</b> choose an area — Place area (click map) or "
                                    "Use current view.")
        else:
            wm, hm = budget.bbox_span_m(self.region)
            est = budget.estimate(self.region, self._detail_factor())
            head_txt = (f"Area ~{wm/1000:.0f}×{hm/1000:.0f} km · {est['px_w']}×{est['px_h']}px "
                        f"· ~{est['result_bytes']/1e9:.1f} GB/year")
            if self.years:
                yrs = "  ".join(f"{y}{' ◀' if y == self.viewing else ''}"
                                for y in sorted(self.years))
                head_txt += f"<br>In memory: {yrs}"
                e = self._yr()
                if e is not None and e.get("nodata", 0.0) > 0.02:
                    head_txt += f" · {e['nodata']*100:.0f}% nodata"
            if not self.years:
                head_txt += ("<br><b>Step 2:</b> " + ("pick two years and Get data & compare."
                             if chg_mode else "pick a year and Get data."))
            elif chg_mode and self.change is None:
                head_txt += "<br><b>Step 2:</b> pick two years and Get data & compare."
            elif not self.classes:
                head_txt += "<br><b>Step 3:</b> Add a class, then drag on the map to paint it."
            self.region_lbl.setText(head_txt)

        has_region = self.region is not None
        self.pin_btn.setEnabled(not loading)
        self.place_btn.setEnabled(not loading)
        self.load_btn.setEnabled(not loading and has_region)
        self.load_btn.setToolTip("Download this year's embeddings for the area."
                                 if has_region else "Choose an area first.")
        self.cancel_btn.setVisible(loading)
        self.basemap_btn.setEnabled(not loading)
        self.pca_btn.setEnabled(loaded and not loading)
        self.pca_btn.setToolTip("False-color view of the embeddings themselves."
                                if loaded else "Get data first.")

        self.add_btn.setEnabled(ready)
        self.add_btn.setToolTip(
            "Create a named class (e.g. water, clearcut)." if ready else
            ("Make a change map first." if chg_mode else "Get data first."))
        for b in (self.rename_btn, self.del_btn):
            b.setEnabled(cls is not None)
        for b in (self.include_btn, self.exclude_btn):
            b.setEnabled(ready and cls is not None)
        self.undo_btn.setEnabled(bool(cls and cls["strokes"]))
        self.clear_btn.setEnabled(bool(cls and cls["strokes"]))
        self.slider.setEnabled(has_sim)
        self.mask_btn.setEnabled(has_sim)
        self.poly_btn.setEnabled(has_sim)

        n_painted = sum(1 for c in self.classes if len(self._class_pos_raw(c)))
        self.classify_btn.setEnabled(ready and n_painted >= 2)
        self.classify_btn.setText("Classify change types" if chg_mode else "Classify land cover")
        self.classify_btn.setToolTip(
            "All classes compete: each pixel gets exactly one label, so classes can't overlap."
            if n_painted >= 2 else "Paint at least two classes first.")
        self.conf_slider.setEnabled(self.class_labels is not None)
        self.save_class_btn.setEnabled(self.class_labels is not None)
        self.apply_head_btn.setEnabled(self.head is not None and ready)
        self.save_head_btn.setEnabled(self.head is not None)

        # change controls
        self.chg_btn.setEnabled(has_region and not loading)
        self.chg_btn.setToolTip(
            "Downloads whichever years aren't in memory (no •), then maps how much each pixel "
            "changed." if has_region else "Choose an area first.")
        has_chg = self.change is not None
        self.chg_slider.setEnabled(has_chg)
        self.chg_auto_btn.setEnabled(has_chg)
        self.chg_poly_btn.setEnabled(has_chg)
        self.chg_save_btn.setEnabled(has_chg)

    # ================= region & load =================
    def _pin_region(self):
        if self.years and not self._confirm_new_region():
            return
        try:
            bbox = self._extent_to_wgs84_bbox()
        except Exception as exc:
            self._msg(f"Could not read canvas extent: {exc}", "Warning")
            return
        if not budget.fits(bbox, self._detail_factor(), self._budget_bytes()):
            est = budget.estimate(bbox, self._detail_factor())
            maxkm = budget.max_box_side_m(self._budget_bytes()) / 1000.0
            self._msg(f"That region would peak at ~{est['peak_bytes']/1e9:.1f} GB to load "
                      f"(budget {self._budget_bytes()/1e9:.1f} GB). Max ~{maxkm:.0f} km/side at "
                      "this memory cap — frame smaller, raise the cap, or use Place region.",
                      "Warning")
            return
        self._apply_region(bbox)

    def _toggle_place(self):
        if self.place_btn.isChecked():
            self.place_tool = PlaceTool(self.canvas, self._on_place)
            self.canvas.setMapTool(self.place_tool)
            self._msg("Click the map to drop an auto-sized region box.", "Info")
        else:
            self._deactivate_place()

    def _deactivate_place(self):
        self.place_btn.setChecked(False)
        if self.place_tool is not None:
            self.canvas.unsetMapTool(self.place_tool)
            self.place_tool = None
        if self.tool is not None:      # restore the seeding tool if a year is loaded
            self.canvas.setMapTool(self.tool)

    def _on_place(self, x, y):
        if self.years and not self._confirm_new_region():
            self._deactivate_place()
            return
        lon, lat = self._project_to_4326(x, y)
        side = budget.max_box_side_m(self._budget_bytes()) * 0.97   # small safety margin
        self._apply_region(self._box_around(lon, lat, side))
        self._deactivate_place()

    def _apply_region(self, bbox):
        # drop loaded years (kept on disk cache); seeds/classes stay (they're embeddings)
        self.years = {}
        self.viewing = None
        for c in self.classes:
            c["sim"] = None
        self._remove_sim_layer()
        self.change = None            # belongs to the old area
        self._pending_change = None
        self._remove_change_layer()
        self.region = bbox
        self._show_region()
        self._sync()

    def _project_to_4326(self, x, y):
        src = QgsProject.instance().crs()
        dst = QgsCoordinateReferenceSystem("EPSG:4326")
        if src != dst:
            p = QgsCoordinateTransform(src, dst, QgsProject.instance()).transform(
                QgsPointXY(x, y))
            return p.x(), p.y()
        return x, y

    def _box_around(self, lon, lat, side_m):
        import math
        half = side_m / 2.0
        dlat = half / 111_320.0
        dlon = half / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
        return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)

    def _confirm_new_region(self):
        from qgis.PyQt.QtWidgets import QMessageBox
        yes = _scoped(QMessageBox, "StandardButton", "Yes")
        no = _scoped(QMessageBox, "StandardButton", "No")
        r = QMessageBox.question(
            self, "New region",
            "Pinning a new region drops the years currently in memory (they stay in the disk "
            "cache). Your classes and seeds are kept. Continue?", yes | no)
        return r == yes

    def _python_exe(self):
        for cand in (os.path.join(sys.exec_prefix, "python.exe"),
                     os.path.join(os.environ.get("PYTHONHOME", ""), "python.exe")):
            if cand and os.path.isfile(cand):
                return cand
        return "python"

    def _on_load(self):
        self._load_year(int(self.year_combo.currentText()))

    def _load_year(self, year):
        if self.proc is not None:
            return
        if year in self.years:            # already in memory -> just view it
            self._set_viewing(year)
            return
        if self.region is None:           # first load pins the current view
            try:
                self.region = self._extent_to_wgs84_bbox()
            except Exception as exc:
                self._msg(f"Could not read canvas extent: {exc}", "Warning")
                return
            self._show_region()
        # Memory budget is the sole gate (tile count no longer rejects; it just tracked area,
        # which the peak-memory check already bounds). Validate here so every path is covered.
        if not budget.fits(self.region, self._detail_factor(), self._budget_bytes()):
            est = budget.estimate(self.region, self._detail_factor())
            maxkm = budget.max_box_side_m(self._budget_bytes()) / 1000.0
            self._msg(f"This region would peak at ~{est['peak_bytes']/1e9:.1f} GB to load "
                      f"(budget {self._budget_bytes()/1e9:.1f} GB). Max ~{maxkm:.0f} km/side — "
                      "raise the Memory cap, use coarser Detail, or place a smaller region.",
                      "Warning")
            return
        spec = {
            "bbox": list(self.region), "year": year, "max_tiles": _TILE_BACKSTOP,
            "target_crs": self._target_crs(), "cache_dir": self._cache_dir(),
            "downsample": self._detail[self.detail_combo.currentText()],
            "out": self.mosaic_base,
        }
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "tessera_paint", "worker.py")
        if not os.path.isfile(worker):
            worker = os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))), "tessera_paint", "worker.py")

        self.proc = QProcess(self)
        self.proc.readyReadStandardOutput.connect(self._proc_output)
        self.proc.finished.connect(lambda code, st: self._proc_done(code, year))
        self._proc_err_text = None
        self._canceled = False
        self.progress.setRange(0, 0)      # busy until first PROG
        self.progress.setVisible(True)
        self.region_lbl.setText(f"Loading {year}…")
        self._sync()
        self.proc.start(self._python_exe(), [worker, json.dumps(spec)])

    def _cancel_load(self):
        if self.proc is not None:
            self._canceled = True
            self.proc.kill()

    def _proc_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in text.splitlines():
            if line.startswith("PROG "):
                parts = line.split(" ", 3)
                try:
                    cur, total = int(parts[1]), int(parts[2])
                    note = parts[3] if len(parts) > 3 else ""
                    if total <= 0:
                        self.progress.setRange(0, 0)         # indeterminate busy (merge phase)
                    else:
                        self.progress.setRange(0, total)     # real download progress
                        self.progress.setValue(cur)
                    self.region_lbl.setText(f"Loading {note}…" if note else "Loading…")
                except (ValueError, IndexError):
                    pass
            elif line.startswith("ERR "):
                self._proc_err_text = line[4:]

    def _proc_done(self, exit_code, year):
        self._proc_output()
        self.proc.deleteLater()
        self.proc = None
        self.progress.setVisible(False)
        if self._canceled:
            self.region_lbl.setText("Load canceled.")
        elif exit_code == 0:
            try:
                self._store_year(year)
            except Exception as exc:
                self._msg(f"Load failed reading result: {exc}", "Warning")
        else:
            self._msg(self._proc_err_text or f"Load failed (exit {exit_code}).", "Warning")
            self._pending_change = None      # don't keep chasing a failed year
        self._sync()
        self._continue_pending_change()

    def _continue_pending_change(self):
        """Change asked for years that weren't loaded — fetch them one at a time, then build."""
        if not self._pending_change or self.proc is not None:
            return
        missing = [y for y in self._pending_change if y not in self.years]
        if missing:
            self._load_year(missing[0])
        else:
            self._pending_change = None
            self._make_change()

    def _store_year(self, year):
        # Evict BEFORE loading the new mosaic so we never hold cap+1 full cubes at once.
        if year not in self.years:
            keep = set(self._pending_change or ())
            while len(self.years) >= _YEAR_CAP:
                evictable = [k for k in self.years if k != self.viewing and k not in keep]
                if not evictable:      # everything resident is needed; make room anyway
                    evictable = [k for k in self.years if k != self.viewing]
                if not evictable:
                    break
                del self.years[evictable[0]]
        mosaic = np.load(self.mosaic_base + ".npy")
        with open(self.mosaic_base + ".json") as f:
            meta = json.load(f)
        transform = rasterio.transform.Affine(*meta["transform"])
        crs = meta["crs"]
        for p in (self.mosaic_base + ".npy", self.mosaic_base + ".json"):
            try:
                os.remove(p)
            except OSError:
                pass
        # keep RAW (for the portable classifier head — a global space that transfers across
        # areas) AND znorm (standardized/normalized, for the fast per-class similarity heatmap)
        znorm, valid, nodata = prepare(mosaic)
        self.years[year] = {
            "raw": mosaic, "znorm": znorm, "valid": valid,
            "transform": transform, "crs": crs, "nodata": nodata,
        }
        if nodata > 0.02:
            self._msg(f"{year}: {nodata*100:.0f}% of this area has no embedding — those pixels "
                      "are shown as 'no data', never as a result.", "Info")
        if self.tool is None:
            self.tool = BrushTool(self.canvas, self._on_stroke)
        self.canvas.setMapTool(self.tool)
        self._set_viewing(year)

    def _set_viewing(self, year):
        self.viewing = year
        self._refresh_change_years()
        for c in self.classes:            # sims are year-specific -> recompute lazily
            c["sim"] = None
        self._recompute()                 # active class for the new year
        self._refresh_all_items()
        self._sync()

    # ================= classes =================
    def _add_class(self):
        name, ok = QInputDialog.getText(self, "Add class", "Class name (e.g. water):")
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(c["name"] == name for c in self.classes):
            self._msg(f'Class "{name}" already exists.', "Info")
            return
        color = QColor(*_CLASS_COLORS[len(self.classes) % len(_CLASS_COLORS)])
        self.classes.append({"name": name, "color": color, "strokes": [],
                             "threshold": 0.5, "sim": None})
        item = QListWidgetItem(self._color_icon(color), "")
        self.class_list.addItem(item)
        self._refresh_item(len(self.classes) - 1)
        self.class_list.setCurrentRow(len(self.classes) - 1)

    def _rename_class(self):
        cls = self._active_class()
        if cls is None:
            return
        name, ok = QInputDialog.getText(self, "Rename class", "New name:", text=cls["name"])
        name = (name or "").strip()
        if ok and name:
            cls["name"] = name
            self._refresh_item(self.class_list.currentRow())

    def _delete_class(self):
        i = self.class_list.currentRow()
        if not (0 <= i < len(self.classes)):
            return
        self._drop_bands(self.classes[i])
        self.classes.pop(i)
        self.class_list.takeItem(i)
        self._remove_sim_layer()
        self._sync()

    def _drop_bands(self, cls):
        for s in cls["strokes"]:
            if s.get("band") is not None:
                self.canvas.scene().removeItem(s["band"])
                s["band"] = None

    def _on_class_changed(self, _row):
        cls = self._active_class()
        for c in self.classes:            # show only the active class's brush strokes
            vis = c is cls
            for s in c["strokes"]:
                if s.get("band") is not None:
                    s["band"].setVisible(vis)
        self._remove_sim_layer()
        if cls is None:
            self._sync()
            return
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(cls["threshold"] * 100)))
        self.slider.blockSignals(False)
        self.thr_label.setText(f"{cls['threshold']:.2f}")
        if cls["sim"] is None and len(self._class_pos(cls)):
            self._recompute()
        elif cls["sim"] is not None:
            self._render_similarity()
        self._sync()

    def _class_pos(self, cls):
        """[n,128] of this class's INCLUDE pixels (empty array if none)."""
        v = [s["vecs"] for s in cls["strokes"] if not s["negative"]]
        return np.concatenate(v, axis=0) if v else np.empty((0, 128), np.float32)

    def _class_neg(self, cls):
        v = [s["vecs"] for s in cls["strokes"] if s["negative"]]
        return np.concatenate(v, axis=0) if v else np.empty((0, 128), np.float32)

    def _mode(self):
        return "change" if self.mode_combo.currentIndex() == 1 else "single"

    def _on_mode_changed(self, *_):
        """One choice drives everything: which year pickers show, what the classifier learns
        from, and whether the change-map panel is relevant."""
        chg = self._mode() == "change"
        self._refresh_change_years()
        self.single_box.setVisible(not chg)
        self.change_box.setVisible(chg)
        self.change_result_box.setVisible(chg)
        self._remove_sim_layer()
        for c in self.classes:          # previews belong to the other feature space
            c["sim"] = None
        self._sync()

    def _feature_key(self):
        """Which stored stroke feature the classifier uses — follows the mode."""
        return "chg" if self._mode() == "change" else "raw"

    def _feature_cube(self):
        """(cube, valid) the head predicts over, matching _feature_key(). None if unavailable."""
        if self._feature_key() == "chg":
            if self.change is None:
                return None, None
            return self.change["feat"], self.change["valid"]
        yr = self._yr()
        return (yr["raw"], yr["valid"]) if yr else (None, None)

    def _feature_meta(self):
        """crs/transform for writing the classified raster in the active feature space."""
        if self._feature_key() == "chg" and self.change is not None:
            return {"crs": self.change["crs"], "transform": self.change["transform"]}
        return self._yr()

    def _class_pos_raw(self, cls):
        k = self._feature_key()
        v = [s[k] for s in cls["strokes"] if not s["negative"] and s.get(k) is not None]
        return np.concatenate(v, axis=0) if v else np.empty((0, 0), np.float32)

    def _class_neg_raw(self, cls):
        k = self._feature_key()
        v = [s[k] for s in cls["strokes"] if s["negative"] and s.get(k) is not None]
        return np.concatenate(v, axis=0) if v else np.empty((0, 0), np.float32)

    def _refresh_item(self, i):
        if not (0 <= i < len(self.classes)):
            return
        c = self.classes[i]
        npos, nneg = len(self._class_pos(c)), len(self._class_neg(c))
        item = self.class_list.item(i)
        item.setIcon(self._color_icon(c["color"]))
        seedtxt = f"{npos}+" + (f" {nneg}−" if nneg else "")
        item.setText(f"{c['name']}   {seedtxt} px   t={c['threshold']:.2f}")

    def _refresh_all_items(self):
        for i in range(len(self.classes)):
            self._refresh_item(i)

    def _active_index(self):
        return self.class_list.currentRow()

    # ================= seeding =================
    def _set_mode(self, mode):
        self.click_mode = mode

    def _on_stroke(self, points, right_button, band):
        """A brush stroke (or single click) -> every pixel under it becomes a training sample."""
        yr = self._yr()
        cls = self._active_class()
        if yr is None or cls is None:
            self.canvas.scene().removeItem(band)
            if cls is None:
                self._msg("Add a class first.", "Info")
            return
        negative = right_button or self.click_mode == "exclude"
        if negative and len(self._class_pos(cls)) == 0:
            self.canvas.scene().removeItem(band)
            self._msg("Paint an include stroke first.", "Info")
            return

        h, w = yr["znorm"].shape[:2]
        r = self.brush_spin.value()
        rows, cols = [], []
        for x, y in points:
            mx, my = self._to_mosaic_xy(x, y)
            r0, c0 = rasterio.transform.rowcol(yr["transform"], mx, my)
            for dr in range(-r, r + 1):                      # disc of radius r around the point
                for dc in range(-r, r + 1):
                    if dr * dr + dc * dc <= r * r:
                        rows.append(r0 + dr)
                        cols.append(c0 + dc)
        rows = np.asarray(rows)
        cols = np.asarray(cols)
        keep = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
        rows, cols = rows[keep], cols[keep]
        if rows.size:
            flat = np.unique(rows.astype(np.int64) * w + cols.astype(np.int64))
            rows, cols = flat // w, flat % w
            keep = yr["valid"][rows, cols]                   # drop nodata pixels
            rows, cols = rows[keep], cols[keep]
        if rows.size == 0:
            self.canvas.scene().removeItem(band)
            self._msg("No usable pixels under that stroke (outside region, or nodata).", "Info")
            return

        band.setVisible(True)
        cls["strokes"].append({
            "negative": negative,
            "vecs": np.asarray(yr["znorm"][rows, cols], dtype=np.float32),   # similarity space
            "raw": np.asarray(yr["raw"][rows, cols], dtype=np.float32),      # head (portable) space
            # baseline+delta at the same pixels, so the SAME strokes can label change types
            "chg": (np.asarray(self.change["feat"][rows, cols], dtype=np.float32)
                    if self.change is not None else None),
            "rows": rows, "cols": cols,      # so a later change map can backfill "chg"
            "band": band,
        })
        self._recompute()

    def _undo_seed(self):
        cls = self._active_class()
        if not cls or not cls["strokes"]:
            return
        s = cls["strokes"].pop()
        if s.get("band") is not None:
            self.canvas.scene().removeItem(s["band"])
        if len(self._class_pos(cls)):
            self._recompute()
        else:
            cls["sim"] = None
            self._remove_sim_layer()
            self._refresh_item(self.class_list.currentRow())
            self._sync()

    def _clear_seeds(self):
        cls = self._active_class()
        if cls is None:
            return
        self._drop_bands(cls)
        cls["strokes"] = []
        cls["sim"] = None
        self._remove_sim_layer()
        self._refresh_item(self.class_list.currentRow())
        self._sync()

    def _recompute(self):
        cls = self._active_class()
        yr = self._yr()
        if cls is None or yr is None:
            return
        pos = self._class_pos(cls)
        neg = self._class_neg(cls)
        neg = neg if len(neg) else None
        if len(pos) == 0:
            cls["sim"] = None
            self._remove_sim_layer()
            self._refresh_item(self.class_list.currentRow())
            self._sync()
            return
        cls["sim"] = score(yr["znorm"], yr["valid"], pos, neg_vectors=neg)
        self._render_similarity()
        self._refresh_item(self.class_list.currentRow())
        self._sync()

    # ================= rendering =================
    def _shade(self, color, f):
        return QColor(int(color.red() * f), int(color.green() * f), int(color.blue() * f))

    def _render_similarity(self):
        cls = self._active_class()
        yr = self._yr()
        if cls is None or cls["sim"] is None or yr is None:
            return
        self._write_tif(self.sim_path, cls["sim"], "float32", yr, compress=None)  # fast, per-click
        key = (id(cls), cls["sim"].shape)
        layer = QgsProject.instance().mapLayer(self.sim_layer_id) if self.sim_layer_id else None
        if layer is None or self._sim_layer_key != key:
            # rebuild only when the class or the grid size changes; seeding the same class
            # just overwrites the file and reloads (no layer/legend churn per click).
            self._remove_sim_layer()
            layer = QgsRasterLayer(self.sim_path, f"similarity — {cls['name']}")
            if not layer.isValid():
                self._msg("Could not render similarity layer.", "Warning")
                return
            color = cls["color"]
            ramp = QgsColorRampShader()
            ramp.setColorRampType(_scoped(QgsColorRampShader, "Type", "Interpolated"))
            ramp.setColorRampItemList([
                QgsColorRampShader.ColorRampItem(0.0, self._shade(color, 0.35), "low"),
                QgsColorRampShader.ColorRampItem(0.6, self._shade(color, 0.7), ""),
                QgsColorRampShader.ColorRampItem(1.0, color, "high"),
            ])
            shader = QgsRasterShader()
            shader.setRasterShaderFunction(ramp)
            renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
            renderer.setClassificationMin(0.0)
            renderer.setClassificationMax(1.0)
            layer.setRenderer(renderer)
            layer.setOpacity(0.7)
            QgsProject.instance().addMapLayer(layer)
            self.sim_layer_id = layer.id()
            self._sim_layer_key = key
        else:
            dp = layer.dataProvider()
            if hasattr(dp, "reloadData"):
                dp.reloadData()
        self._apply_threshold()

    def _apply_threshold(self):
        layer = QgsProject.instance().mapLayer(self.sim_layer_id) if self.sim_layer_id else None
        if layer is None:
            return
        px = QgsRasterTransparency.TransparentSingleValuePixel()
        px.min = -1.0
        px.max = self._threshold()
        px.percentTransparent = 100.0
        tr = QgsRasterTransparency()
        tr.setTransparentSingleValuePixelList([px])
        layer.renderer().setRasterTransparency(tr)
        layer.triggerRepaint()

    def _remove_sim_layer(self):
        if self.sim_layer_id:
            QgsProject.instance().removeMapLayer(self.sim_layer_id)
            self.sim_layer_id = None
        self._sim_layer_key = None

    # ================= threshold / export =================
    def _threshold(self):
        return self.slider.value() / 100.0

    def _on_threshold_label(self):
        # instant: number + class row; debounced: the raster re-render (feels live, stays smooth)
        t = self._threshold()
        self.thr_label.setText(f"{t:.2f}")
        cls = self._active_class()
        if cls is not None:
            cls["threshold"] = t
            self._refresh_item(self.class_list.currentRow())
        if self.sim_layer_id:
            self._thr_timer.start()

    def _create_mask(self):
        cls = self._active_class()
        yr = self._yr()
        if cls is None or cls["sim"] is None or yr is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save mask", f"{cls['name']}_mask.tif", "GeoTIFF (*.tif)")
        if not path:
            return
        m = (make_mask(cls["sim"], cls["threshold"]) * 255).astype(np.uint8)
        self._write_tif(path, m, "uint8", yr, nodata=0)
        layer = QgsRasterLayer(path, os.path.splitext(os.path.basename(path))[0])
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
        self._msg(f"Mask saved: {path}", "Success")

    def _polygonize(self):
        cls = self._active_class()
        yr = self._yr()
        if cls is None or cls["sim"] is None or yr is None:
            return
        mask_path = os.path.join(self.tmpdir, "mask.tif")
        m = (make_mask(cls["sim"], cls["threshold"]) * 255).astype(np.uint8)
        self._write_tif(mask_path, m, "uint8", yr, nodata=0)
        try:
            import processing
            res = processing.run("gdal:polygonize", {
                "INPUT": mask_path, "BAND": 1, "FIELD": "value",
                "EIGHT_CONNECTEDNESS": False, "OUTPUT": "TEMPORARY_OUTPUT",
            })
            layer = self.iface.addVectorLayer(res["OUTPUT"], f"{cls['name']} (polygons)", "ogr")
            if layer is None:
                self._msg("Polygonize ran but the layer could not load.", "Warning")
                return
            layer.startEditing()
            layer.addAttribute(QgsField("class", _qvariant_string()))
            idx = layer.fields().indexOf("class")
            for f in layer.getFeatures():
                layer.changeAttributeValue(f.id(), idx, cls["name"])
            layer.commitChanges()
        except Exception as exc:
            self._msg(f"Polygonize failed: {exc}", "Warning")

    # ================= change =================
    def _refresh_change_years(self):
        """Any year can be picked; ones already in memory are marked. Missing years are
        fetched automatically when you hit Make change map."""
        for combo, default in ((self.chg_a, _YEARS[0]), (self.chg_b, _DEFAULT_YEAR)):
            cur = self._combo_year(combo)
            combo.blockSignals(True)
            combo.clear()
            for y in _YEARS:
                combo.addItem(f"{y} •" if int(y) in self.years else y)   # dot = loaded
            combo.setCurrentIndex(_YEARS.index(str(cur)) if cur and str(cur) in _YEARS
                                  else _YEARS.index(default))
            combo.blockSignals(False)

    def _combo_year(self, combo):
        txt = combo.currentText().split()[0] if combo.currentText() else ""
        return int(txt) if txt.isdigit() else None

    def _make_change(self):
        ya, yb = self._combo_year(self.chg_a), self._combo_year(self.chg_b)
        if ya is None or yb is None:
            self._msg("Pick a From year and a To year.", "Info")
            return
        if ya == yb:
            self._msg("Pick two different years.", "Info")
            return
        if self.region is None:
            self._msg("Pin a region first (Place region, or Load a year).", "Info")
            return
        missing = [y for y in (ya, yb) if y not in self.years]
        if missing:
            if self.proc is not None:
                return
            self._pending_change = (ya, yb)
            self._msg(f"Fetching {', '.join(str(m) for m in missing)} for the change map…",
                      "Info")
            self._load_year(missing[0])
            return
        A, B = self.years[ya], self.years[yb]
        try:
            # geotessera derives resolution per fetch, so years of the same region can differ
            # sub-pixel; put B on A's exact grid before differencing.
            raw_a, raw_b, transform = change.align(
                A["raw"], A["transform"], B["raw"], B["transform"], A["crs"])
            va = A["valid"]                                  # A is the reference grid, unchanged
            nb = np.linalg.norm(raw_b, axis=2)              # B was resampled -> recompute validity
            vb = np.isfinite(nb) & (nb > 0)
            valid = va & vb
            cov = change.coverage(va, vb)
            score_map = change.change_score(raw_a, raw_b, valid)
        except Exception as exc:
            self._msg(f"Change failed: {exc}", "Warning")
            return
        self.change = {
            "a": ya, "b": yb, "score": score_map, "valid": valid, "coverage": cov,
            "feat": change.ChangeFeatures(raw_a, raw_b),
            "transform": transform, "crs": A["crs"],
        }
        # strokes painted before this change map get their change features filled in, so
        # existing paint can label change types without repainting
        for c in self.classes:
            for st in c["strokes"]:
                if st.get("rows") is not None:
                    st["chg"] = np.asarray(
                        self.change["feat"][st["rows"], st["cols"]], dtype=np.float32)
        # open on an automatically-chosen cutoff (Otsu) instead of an arbitrary number
        t = change.otsu_threshold(score_map, valid)
        self.chg_slider.blockSignals(True)
        self.chg_slider.setValue(int(round(t * 100)))
        self.chg_slider.blockSignals(False)
        self.chg_lbl.setText(f"{t:.2f}")
        self._render_change()
        pct = 100.0 * float((score_map[valid] >= t).mean()) if valid.any() else 0.0
        # a pixel needs BOTH years to be comparable — say plainly how much was lost, and to which
        gap = 1.0 - float(valid.mean())
        note = ""
        if gap > 0.005:
            self._render_coverage()
            note = (f" {gap*100:.0f}% has no data in one or both years — shown as its own "
                    "class on the map, not as 'no change'.")
        self._msg(f"Change map {ya}→{yb}: {pct:.1f}% of comparable pixels above the cutoff.{note}",
                  "Success")
        self.chg_hint.setText(
            f"{ya}→{yb} · {(1-gap)*100:.0f}% comparable. Paint kinds of change (clearcut, "
            "regrowth, flooding…) and Classify to label them.")
        self._sync()

    def _auto_threshold(self):
        if self.change is None:
            return
        t = change.otsu_threshold(self.change["score"], self.change["valid"])
        self.chg_slider.setValue(int(round(t * 100)))     # fires the live re-render
        pct = 100.0 * float((self.change["score"][self.change["valid"]] >= t).mean())
        self._msg(f"Auto cutoff {t:.2f} — {pct:.1f}% of comparable pixels flagged as changed.",
                  "Info")

    def _chg_threshold(self):
        return self.chg_slider.value() / 100.0

    def _on_chg_threshold(self):
        self.chg_lbl.setText(f"{self._chg_threshold():.2f}")
        if self.change is not None:
            self._chg_timer.start()

    def _render_change(self):
        if self.change is None:
            return
        c = self.change
        self._write_tif(self.change_path, c["score"], "float32",
                        {"crs": c["crs"], "transform": c["transform"]}, compress=None)
        key = (c["a"], c["b"], c["score"].shape)
        layer = (QgsProject.instance().mapLayer(self.change_layer_id)
                 if self.change_layer_id else None)
        if layer is None or self._change_layer_key != key:
            self._remove_change_layer()
            layer = QgsRasterLayer(self.change_path, f"change {c['a']}→{c['b']}")
            if not layer.isValid():
                self._msg("Could not render the change layer.", "Warning")
                return
            ramp = QgsColorRampShader()
            ramp.setColorRampType(_scoped(QgsColorRampShader, "Type", "Interpolated"))
            ramp.setColorRampItemList([
                QgsColorRampShader.ColorRampItem(0.0, QColor(40, 40, 60), "none"),
                QgsColorRampShader.ColorRampItem(0.5, QColor(230, 130, 30), ""),
                QgsColorRampShader.ColorRampItem(1.0, QColor(255, 240, 90), "max"),
            ])
            shader = QgsRasterShader()
            shader.setRasterShaderFunction(ramp)
            r = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
            r.setClassificationMin(0.0)
            r.setClassificationMax(1.0)
            layer.setRenderer(r)
            layer.setOpacity(0.8)
            QgsProject.instance().addMapLayer(layer)
            self.change_layer_id = layer.id()
            self._change_layer_key = key
        else:
            dp = layer.dataProvider()
            if hasattr(dp, "reloadData"):
                dp.reloadData()
        self._apply_chg_threshold()

    def _apply_chg_threshold(self):
        layer = (QgsProject.instance().mapLayer(self.change_layer_id)
                 if self.change_layer_id else None)
        if layer is None:
            return
        px = QgsRasterTransparency.TransparentSingleValuePixel()
        px.min = -1.0
        px.max = self._chg_threshold()
        px.percentTransparent = 100.0
        tr = QgsRasterTransparency()
        tr.setTransparentSingleValuePixelList([px])
        layer.renderer().setRasterTransparency(tr)
        layer.triggerRepaint()
        # the change-type classification is masked by this same threshold -> keep it in sync
        if self.class_labels is not None and self.head_feature == "chg":
            self._render_classified()

    def _remove_change_layer(self):
        for attr in ("change_layer_id", "coverage_layer_id"):
            lid = getattr(self, attr, None)
            if lid:
                QgsProject.instance().removeMapLayer(lid)
                setattr(self, attr, None)
        self._change_layer_key = None

    def _change_mask(self):
        return (self.change["score"] >= self._chg_threshold()).astype(np.uint8) * 255

    def _save_change(self):
        if self.change is None:
            return
        c = self.change
        path, _ = QFileDialog.getSaveFileName(
            self, "Save change", f"change_{c['a']}_{c['b']}.tif", "GeoTIFF (*.tif)")
        if not path:
            return
        yrmeta = {"crs": c["crs"], "transform": c["transform"]}
        self._write_tif(path, c["score"], "float32", yrmeta)
        layer = QgsRasterLayer(path, os.path.splitext(os.path.basename(path))[0])
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
        if self.change.get("coverage") is not None and self.change["coverage"].any():
            cov_path = os.path.splitext(path)[0] + "_nodata.tif"
            self._write_tif(cov_path, self.change["coverage"], "uint8", yrmeta, nodata=0)
            self._msg(f"Change map saved: {path} (+ {os.path.basename(cov_path)})", "Success")
        else:
            self._msg(f"Change map saved: {path}", "Success")

    def _render_coverage(self):
        """Show WHERE data is missing, as its own layer. Without this, a gap renders as
        score 0 — visually identical to 'nothing changed', which is worse than no answer."""
        c = self.change
        if c is None or c.get("coverage") is None:
            return
        path = os.path.join(self.tmpdir, "coverage.tif")
        meta = {"crs": c["crs"], "transform": c["transform"]}
        self._write_tif(path, c["coverage"], "uint8", meta, nodata=0)
        if self.coverage_layer_id:
            QgsProject.instance().removeMapLayer(self.coverage_layer_id)
            self.coverage_layer_id = None
        layer = QgsRasterLayer(path, f"no data {c['a']}→{c['b']}")
        if not layer.isValid():
            return
        entries = [
            QgsPalettedRasterRenderer.Class(change.COV_MISSING_A, QColor(210, 60, 200),
                                            f"no data in {c['a']}"),
            QgsPalettedRasterRenderer.Class(change.COV_MISSING_B, QColor(90, 190, 230),
                                            f"no data in {c['b']}"),
            QgsPalettedRasterRenderer.Class(change.COV_MISSING_BOTH, QColor(120, 120, 120),
                                            "no data in both"),
        ]
        layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, entries))
        layer.setOpacity(0.85)
        QgsProject.instance().addMapLayer(layer)
        self.coverage_layer_id = layer.id()

    def _polygonize_change(self):
        """One layer, three classes: changed, and the two flavours of missing data — so a gap
        is never silently exported as 'unchanged'."""
        if self.change is None:
            return
        c = self.change
        cat = change.categorize(c["score"], c["coverage"], self._chg_threshold())
        cat_path = os.path.join(self.tmpdir, "change_cat.tif")
        self._write_tif(cat_path, cat, "uint8",
                        {"crs": c["crs"], "transform": c["transform"]}, nodata=0)
        layer = self._run_polygonize(cat_path, f"change {c['a']}→{c['b']} (polygons)")
        if layer is None:
            return
        labels = dict(change.CATEGORY_LABELS)
        labels[2] = f"no data (one year)"
        layer.startEditing()
        layer.addAttribute(QgsField("class", _qvariant_string()))
        idx = layer.fields().indexOf("class")
        vidx = layer.fields().indexOf("value")
        for f in layer.getFeatures():
            try:
                v = int(f.attributes()[vidx])
            except (TypeError, ValueError):
                v = 0
            layer.changeAttributeValue(f.id(), idx, labels.get(v, "unchanged"))
        layer.commitChanges()
        n_nodata = int(((cat == 2) | (cat == 3)).sum())
        if n_nodata:
            self._msg("Polygons include 'no data' classes where a year was missing.", "Info")

    def _run_polygonize(self, raster_path, layer_name, attr=None):
        """Shared gdal:polygonize + optional 'class' attribute stamp."""
        try:
            import processing
            res = processing.run("gdal:polygonize", {
                "INPUT": raster_path, "BAND": 1, "FIELD": "value",
                "EIGHT_CONNECTEDNESS": False, "OUTPUT": "TEMPORARY_OUTPUT",
            })
            layer = self.iface.addVectorLayer(res["OUTPUT"], layer_name, "ogr")
            if layer is None:
                self._msg("Polygonize ran but the layer could not load.", "Warning")
                return None
            if attr is not None:
                layer.startEditing()
                layer.addAttribute(QgsField("class", _qvariant_string()))
                idx = layer.fields().indexOf("class")
                for f in layer.getFeatures():
                    layer.changeAttributeValue(f.id(), idx, attr)
                layer.commitChanges()
            return layer
        except Exception as exc:
            self._msg(f"Polygonize failed: {exc}", "Warning")
            return None

    # ================= classify (all classes compete) =================
    def _conf(self):
        return self.conf_slider.value() / 100.0

    def _on_conf_changed(self):
        self.conf_label.setText(f"{self._conf():.2f}")
        if self.class_labels is not None:
            self._conf_timer.start()

    def _classify(self):
        cube, _ = self._feature_cube()
        if cube is None:
            self._msg("Make a change map first to classify change types."
                      if self._feature_key() == "chg" else "Load a year first.", "Info")
            return
        painted = [c for c in self.classes if len(self._class_pos_raw(c))]
        if len(painted) < 2:
            self._msg("Paint at least two classes — they classify by competing with each other.",
                      "Info")
            return
        # train on RAW embeddings (a global space) so the head transfers to other areas
        train_sets = [self._class_pos_raw(c) for c in painted]
        names = [c["name"] for c in painted]
        colors = [(c["color"].red(), c["color"].green(), c["color"].blue()) for c in painted]
        # pooled excludes from every class become one shared "other" -> real abstention target
        negs = [n for n in (self._class_neg_raw(c) for c in self.classes) if len(n)]
        if negs:
            train_sets.append(np.concatenate(negs, axis=0))
            names.append("other")
            colors.append((150, 150, 150))
        try:
            self.head = head.train(train_sets)
        except Exception as exc:
            self._msg(f"Classify failed: {exc}", "Warning")
            return
        self.head_names, self.head_colors = names, colors
        self.head_feature = self._feature_key()
        n_px = sum(len(t) for t in train_sets)
        self._msg(f"Trained on {n_px} painted pixels across {len(names)} classes. "
                  "Load another area and 'Apply head' to reuse it.", "Success")
        self._apply_head()

    def _apply_head(self):
        """Predict the current area with the trained/loaded head — no retraining. This is how a
        classifier made on one area is reused on new ones."""
        if self.head is None:
            return
        cube, valid = self._feature_cube()
        if cube is None:
            self._msg("Nothing to classify: load a year (or make a change map for "
                      "change features).", "Info")
            return
        if self.head.get("d") not in (None, cube.shape[2]):
            self._msg("This head was trained on different features "
                      f"({self.head.get('d')}-d). Switch the Features selector to match.",
                      "Warning")
            return
        try:
            labels, margin = head.predict(cube, valid, self.head)
        except Exception as exc:
            self._msg(f"Apply failed: {exc}", "Warning")
            return
        self.class_labels, self.class_margin = labels, margin
        self.class_names = list(self.head_names)
        self.class_colors = [QColor(*c) for c in self.head_colors]
        self._render_classified()
        self._sync()

    def _save_head(self):
        if self.head is None:
            self._msg("Train a classifier first (Classify).", "Info")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save classifier", "classifier.npz",
                                              "Classifier (*.npz)")
        if not path:
            return
        head.save(self.head, path)
        with open(os.path.splitext(path)[0] + ".json", "w") as f:
            json.dump({"names": self.head_names, "colors": self.head_colors}, f)
        self._msg(f"Classifier saved: {path}", "Success")

    def _load_head(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load classifier", "", "Classifier (*.npz)")
        if not path:
            return
        try:
            self.head = head.load(path)
            with open(os.path.splitext(path)[0] + ".json") as f:
                meta = json.load(f)
            self.head_names = meta["names"]
            self.head_colors = [tuple(c) for c in meta["colors"]]
        except Exception as exc:
            self._msg(f"Could not load classifier: {exc}", "Warning")
            return
        self._msg(f"Loaded classifier with {len(self.head_names)} classes. "
                  "Load an area and 'Apply head'.", "Success")
        if self._yr() is not None:
            self._apply_head()
        self._sync()

    def _nodata_index(self):
        """Index used for the explicit 'no data' category (one past the real classes)."""
        return len(self.class_names)

    def _classified_labels(self):
        """The labels actually shown/exported.

        Three states are kept distinct on purpose, because conflating them is what made the
        output confusing: a real class, -1 = not classified (unchanged, or below confidence,
        rendered transparent), and an explicit 'no data' category where an embedding was
        missing. In change mode the result is also RESTRICTED to pixels above the change
        cutoff, so unchanged ground is never forced into a change type.
        """
        lab = head.apply_confidence(self.class_labels, self.class_margin, self._conf()).copy()
        chg_mode = self.head_feature == "chg" and self.change is not None
        if chg_mode:
            lab[self.change["score"] < self._chg_threshold()] = -1   # unchanged -> transparent
            missing = ~self.change["valid"]
        else:
            yr = self._yr()
            missing = ~yr["valid"] if yr is not None else None
        if missing is not None and missing.shape == lab.shape and missing.any():
            lab[missing] = self._nodata_index()      # visible, labelled — never a silent guess
        return lab

    def _palette_entries(self, labels=None):
        entries = [QgsPalettedRasterRenderer.Class(i, self.class_colors[i], self.class_names[i])
                   for i in range(len(self.class_names))]
        if labels is None or (labels == self._nodata_index()).any():
            entries.append(QgsPalettedRasterRenderer.Class(
                self._nodata_index(), QColor(120, 120, 120), "no data"))
        return entries

    def _render_classified(self):
        if self.class_labels is None or self._feature_meta() is None:
            return
        lab = self._classified_labels()
        self._write_tif(self.class_path, lab, "int16", self._feature_meta(),
                        nodata=-1, compress=None)
        key = (tuple(self.class_names), self.class_labels.shape,
               bool((lab == self._nodata_index()).any()))
        layer = (QgsProject.instance().mapLayer(self.class_layer_id)
                 if self.class_layer_id else None)
        if layer is None or self._class_layer_key != key:
            self._remove_class_layer()
            layer = QgsRasterLayer(self.class_path, "TESSERA classes")
            if not layer.isValid():
                self._msg("Could not render the classified layer.", "Warning")
                return
            layer.setRenderer(QgsPalettedRasterRenderer(
                layer.dataProvider(), 1, self._palette_entries(lab)))
            layer.setOpacity(0.75)
            QgsProject.instance().addMapLayer(layer)
            self.class_layer_id = layer.id()
            self._class_layer_key = key
        else:
            dp = layer.dataProvider()
            if hasattr(dp, "reloadData"):
                dp.reloadData()
            layer.triggerRepaint()

    def _remove_class_layer(self):
        if self.class_layer_id:
            QgsProject.instance().removeMapLayer(self.class_layer_id)
            self.class_layer_id = None
        self._class_layer_key = None

    def _save_classified(self):
        if self.class_labels is None:
            self._msg("Run Classify first.", "Info")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save classified", "classified.tif",
                                              "GeoTIFF (*.tif)")
        if not path:
            return
        lab = self._classified_labels()
        self._write_tif(path, lab, "int16", self._feature_meta(), nodata=-1)
        layer = QgsRasterLayer(path, os.path.splitext(os.path.basename(path))[0])
        if layer.isValid():
            layer.setRenderer(QgsPalettedRasterRenderer(
                layer.dataProvider(), 1, self._palette_entries(lab)))
            QgsProject.instance().addMapLayer(layer)
        # polygons, with the class NAME on every feature
        try:
            import processing
            res = processing.run("gdal:polygonize", {
                "INPUT": path, "BAND": 1, "FIELD": "value",
                "EIGHT_CONNECTEDNESS": False, "OUTPUT": "TEMPORARY_OUTPUT",
            })
            vec = self.iface.addVectorLayer(res["OUTPUT"], "TESSERA classes (polygons)", "ogr")
            if vec is not None:
                vec.startEditing()
                vec.addAttribute(QgsField("class", _qvariant_string()))
                idx = vec.fields().indexOf("class")
                vidx = vec.fields().indexOf("value")
                for f in vec.getFeatures():
                    try:
                        i = int(f.attributes()[vidx])
                        name = (self.class_names[i] if 0 <= i < len(self.class_names)
                                else ("no data" if i == self._nodata_index() else ""))
                    except (TypeError, ValueError):
                        name = ""
                    vec.changeAttributeValue(f.id(), idx, name)
                vec.commitChanges()
        except Exception as exc:
            self._msg(f"Saved raster; polygonize failed: {exc}", "Warning")
            return
        self._msg(f"Classified saved: {path}", "Success")

    # ================= visibility: basemap + preview =================
    def _add_basemap(self):
        year = int(self.year_combo.currentText())
        use = year if year in _EOX_YEARS else min(_EOX_YEARS, key=lambda y: abs(y - year))
        template = (f"https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-{use}_3857"
                    "/default/g/{z}/{y}/{x}.jpg")
        uri = f"type=xyz&url={quote(template, safe='')}&zmax=18&zmin=0"
        layer = QgsRasterLayer(uri, f"Sentinel-2 cloudless {use} (EOX)", "wms")
        if not layer.isValid():
            self._msg("Could not add the EOX basemap (network?).", "Warning")
            return
        QgsProject.instance().addMapLayer(layer, False)
        QgsProject.instance().layerTreeRoot().addLayer(layer)
        if use != year:
            self._msg(f"EOX has no {year} imagery; showing {use} instead.", "Info")

    def _show_pca(self):
        yr = self._yr()
        if yr is None:
            return
        self._remove_pca_layer()
        rgb = pca_rgb(yr["znorm"])
        self._write_rgb_tif(self.pca_path, rgb, yr)
        layer = QgsRasterLayer(self.pca_path, f"embedding PCA — {self.viewing}")
        if not layer.isValid():
            self._msg("Could not render the PCA preview.", "Warning")
            return
        QgsProject.instance().addMapLayer(layer)
        self.pca_layer_id = layer.id()

    def _remove_pca_layer(self):
        if self.pca_layer_id:
            QgsProject.instance().removeMapLayer(self.pca_layer_id)
            self.pca_layer_id = None

    # ================= region outline =================
    def _show_region(self):
        self._remove_region_band()
        if self.region is None:
            return
        minx, miny, maxx, maxy = self.region
        rect = QgsRectangle(minx, miny, maxx, maxy)
        src = QgsCoordinateReferenceSystem("EPSG:4326")
        dst = QgsProject.instance().crs()
        if src != dst:
            rect = QgsCoordinateTransform(src, dst, QgsProject.instance()).transformBoundingBox(rect)
        try:
            geomtype = Qgis.GeometryType.Polygon
        except AttributeError:
            from qgis.core import QgsWkbTypes
            geomtype = QgsWkbTypes.PolygonGeometry
        band = QgsRubberBand(self.canvas, geomtype)
        band.setToGeometry(QgsGeometry.fromRect(rect), None)
        band.setColor(QColor(255, 140, 0))
        band.setFillColor(QColor(0, 0, 0, 0))
        band.setWidth(2)
        self.region_band = band

    def _remove_region_band(self):
        if self.region_band is not None:
            self.canvas.scene().removeItem(self.region_band)
            self.region_band = None

    # ================= geo helpers =================
    def _write_rgb_tif(self, path, rgb, yr):
        h, w, _ = rgb.shape
        with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=3,
                           dtype="uint8", crs=yr["crs"], transform=yr["transform"],
                           photometric="RGB", compress="lzw") as ds:
            for b in range(3):
                ds.write(rgb[:, :, b], b + 1)

    def _write_tif(self, path, array2d, dtype, yr, nodata=None, compress="lzw"):
        h, w = array2d.shape
        kw = {"compress": compress} if compress else {}
        with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                           dtype=dtype, crs=yr["crs"], transform=yr["transform"],
                           nodata=nodata, **kw) as ds:
            ds.write(array2d.astype(dtype), 1)

    def _cache_dir(self):
        return os.path.join(QgsApplication.qgisSettingsDirPath(), "cache",
                            "tessera_paint", "embeddings")

    def _extent_to_wgs84_bbox(self):
        ext = self.canvas.extent()
        src = QgsProject.instance().crs()
        dst = QgsCoordinateReferenceSystem("EPSG:4326")
        r = QgsCoordinateTransform(src, dst, QgsProject.instance()).transformBoundingBox(ext)
        return (r.xMinimum(), r.yMinimum(), r.xMaximum(), r.yMaximum())

    def _target_crs(self):
        authid = QgsProject.instance().crs().authid()
        return authid if authid.startswith("EPSG:") else "EPSG:3857"

    def _to_mosaic_xy(self, x, y):
        proj_crs = QgsProject.instance().crs()
        mos_crs = QgsCoordinateReferenceSystem(self._yr()["crs"])
        if proj_crs != mos_crs:
            p = QgsCoordinateTransform(proj_crs, mos_crs, QgsProject.instance()).transform(
                QgsPointXY(x, y))
            return p.x(), p.y()
        return x, y

    def _msg(self, text, level="Info"):
        self.iface.messageBar().pushMessage(
            "TESSERA Paint", text, level=_scoped(Qgis, "MessageLevel", level), duration=5)

    def cleanup(self):
        if self.proc is not None:
            self._canceled = True
            self.proc.kill()
        for c in self.classes:
            self._drop_bands(c)
        self.classes = []
        self._remove_sim_layer()
        self._remove_pca_layer()
        self._remove_class_layer()
        self._remove_change_layer()
        self._remove_region_band()
        for t in (self.place_tool, self.tool):
            if t is not None:
                self.canvas.unsetMapTool(t)
        self.place_tool = None
        self.tool = None
