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
        self.n_tiles = 0
        self._canceled = False
        self._tmp_root = None

        self._thr_timer = QTimer(self)
        self._thr_timer.setSingleShot(True)
        self._thr_timer.setInterval(60)
        self._thr_timer.timeout.connect(self._apply_threshold)

        self._build_ui()
        self._sync()

    # ---------------- UI ----------------
    def _build_ui(self):
        outer = QWidget()
        lay = QVBoxLayout(outer)

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
        lay.addLayout(drow)

        self.area_lbl = QLabel("Draw an area to begin.")
        self.area_lbl.setWordWrap(True)
        lay.addWidget(self.area_lbl)

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
        self.detail.setToolTip(
            "Output pixel size — and, above 10 m, how much gets downloaded. A coarser setting "
            "reads the data's own built-in reduced-resolution copies, so a large area takes "
            "minutes instead of hours (a 200x200 km job: ~59 GB at 10 m, ~2 GB at 100 m). The "
            "160 m embedding cells behind the classifier are identical either way, so coarse "
            "Detail still gives you polygons and classes. Coarse maps do read very slightly "
            "conservative near the cutoff. On a small area, use 10 m — a coarse setting there "
            "costs the same download and gives you a handful of pixels.")
        yrow.addWidget(self.detail)
        lay.addLayout(yrow)

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
        lay.addLayout(orow)

        rrow = QHBoxLayout()
        self.run_btn = QPushButton("Make change map")
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
        lay.addLayout(trow)

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
        lay.addLayout(prow)
        from .engine import basemap as _BM
        credit = QLabel(f"{_BM.ATTRIBUTION}  ·  {_BM.LICENCE}")
        credit.setWordWrap(True)
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet("color: palette(mid); font-size: 9px;")
        lay.addWidget(credit)
        self._sync_photos()

        erow = QHBoxLayout()
        self.poly_btn = QPushButton("Polygonize")
        self.poly_btn.setToolTip("Turn the changed pixels into editable polygons.")
        self.poly_btn.clicked.connect(self._polygonize)
        erow.addWidget(self.poly_btn)
        self.save_btn = QPushButton("Save as GeoTIFF…")
        self.save_btn.setToolTip("Merge the tiles into a single file.")
        self.save_btn.clicked.connect(self._save_geotiff)
        erow.addWidget(self.save_btn)
        lay.addLayout(erow)

        # Stays collapsed and disabled until a change map exists. Someone who only wants a
        # change map should never have to look at any of this.
        self.classify_group = _GroupBox("4 · Classify change")
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

    def _sync(self):
        running = self.proc is not None
        has_area = self.bbox is not None
        has_result = self.layer_id is not None
        self.run_btn.setEnabled(has_area and not running)
        self.run_btn.setToolTip("" if has_area else "Draw an area first.")
        self.cancel_btn.setVisible(running)
        self.draw_btn.setEnabled(not running)
        self.clear_area_btn.setEnabled(
            not running and (has_area or self.area_band is not None))
        self.browse_btn.setEnabled(not running)
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
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the change map")
        if d:
            self.out_edit.setText(d)

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
        if chosen:
            self.out_dir = os.path.join(chosen, f"change_{ya}_{yb}")
        else:
            self._tmp_root = self._tmp_root or tempfile.mkdtemp(prefix="embed_cd_")
            self.out_dir = os.path.join(self._tmp_root, f"change_{ya}_{yb}")
        os.makedirs(self.out_dir, exist_ok=True)
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
        self._remove_layer()
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
                QgsProject.instance().addMapLayer(cov)   # added first -> ends up underneath
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
            QgsProject.instance().addMapLayer(layer)
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
