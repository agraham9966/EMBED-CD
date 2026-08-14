"""The 'what happened here' half of the dock: polygons, labels, and the editable head.

Lives in its own file because the dock is already the big one, and this is a self-contained
panel: give it a finished change job and it owns everything from there.

Labelling is click-a-polygon-on-the-map, via LabelTool. An earlier version made the user drive
QGIS's own selection first; that was a bad call — it silently depends on the polygon layer being
the active one, and gives no feedback when it isn't. Selection is still there as a bulk path.
"""
import os

import numpy as np

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QDoubleSpinBox, QInputDialog, QFileDialog, QMessageBox, QSlider, QCheckBox,
    QProgressDialog, QApplication,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsFillSymbol,
    QgsFeatureRequest, QgsRectangle, QgsPointXY,
)
from qgis.gui import QgsMapTool

UNKNOWN = "unknown"
_PALETTE = ["#d85a30", "#1d9e75", "#7f77dd", "#d4537e", "#378add", "#ba7517",
            "#639922", "#993556"]


def _scoped(owner, category, name):
    try:
        return getattr(getattr(owner, category), name)
    except AttributeError:
        return getattr(owner, name)


def _qv(kind):
    try:
        from qgis.PyQt.QtCore import QMetaType
        return {"str": QMetaType.Type.QString, "float": QMetaType.Type.Double,
                "int": QMetaType.Type.Int}[kind]
    except (ImportError, AttributeError, KeyError):
        from qgis.PyQt.QtCore import QVariant
        return {"str": QVariant.String, "float": QVariant.Double, "int": QVariant.Int}[kind]


class LabelTool(QgsMapTool):
    """Click a polygon on the map to label it with the current class; right-click to unlabel.

    This exists because making the user drive QGIS's selection first was a bad call: it depends
    on the right layer being active, gives no feedback when it isn't, and is an extra concept
    for something that should be one click.
    """

    def __init__(self, canvas, panel):
        super().__init__(canvas)
        self.panel = panel

    def canvasReleaseEvent(self, event):
        pt = self.toMapCoordinates(event.pos())
        right = event.button() == _scoped(Qt, "MouseButton", "RightButton")
        self.panel.label_at(pt, clear=right)


class ClassifyPanel(QWidget):
    """Owns the polygon layer, the labels, and the head. `host` is the dock, consulted for the
    job that just finished (output folder, VRT, years, threshold)."""

    def __init__(self, host, iface):
        super().__init__()
        self.host = host
        self.iface = iface
        self.layer = None
        self.vectors = None            # [N, D] aligned to feature id
        self.polys = []
        self._crs = None           # CRS the current polygons were cut in
        self._cut_threshold = None  # the cutoff these polygons were made at
        self.labels = {}               # row -> class name, for the CURRENT polygon set
        # Examples banked from earlier polygon sets. A class is the user's vocabulary and the
        # expensive part of their work, so it must outlive a re-polygonize or a new area; only
        # the row->name map is tied to the current set and gets cleared.
        self.class_vectors = {}        # name -> [vector, ...]
        self._fid_row = {}             # feature id -> row, built once per layer
        self.classes = []              # ordered class names
        self.colors = {}
        self.head = None
        self.pred = None
        self.scores = None
        self.tool = None
        self.paused = False
        self._build()

    # ---------------- ui ----------------
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self.make_btn = QPushButton("Make polygons")
        self.make_btn.setToolTip("Cut the change map at the current cutoff and give every "
                                 "object the embedding of what it covers.")
        self.make_btn.clicked.connect(self.make_polygons)
        row.addWidget(self.make_btn)
        row.addWidget(QLabel("min size"))
        self.min_area = QDoubleSpinBox()
        self.min_area.setRange(0.01, 1000.0)
        self.min_area.setValue(1.0)
        self.min_area.setSuffix(" ha")
        self.min_area.setToolTip("Ignore objects smaller than this. Without it a low cutoff "
                                 "produces thousands of speckles.")
        row.addWidget(self.min_area)
        lay.addLayout(row)

        self.count_lbl = QLabel("Run a change map first.")
        self.count_lbl.setWordWrap(True)
        lay.addWidget(self.count_lbl)

        self.list = QListWidget()
        self.list.setMaximumHeight(120)
        self.list.setToolTip("Click to pick the class you are labelling with. "
                             "Double-click to select that class's polygons on the map — "
                             "double-click 'unknown' to see everything still unaccounted for.")
        self.list.itemDoubleClicked.connect(self._select_class_on_map)
        self.list.currentRowChanged.connect(lambda _r: self._show_selected())
        lay.addWidget(self.list)

        crow = QHBoxLayout()
        for text, slot, tip in (("+ Add", self.add_class, "Add a class."),
                                ("Rename", self.rename_class, "Rename the selected class."),
                                ("Delete", self.delete_class, "Delete the selected class.")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            crow.addWidget(b)
        lay.addLayout(crow)

        self.label_btn = QPushButton("Label by clicking the map")
        self.label_btn.setCheckable(True)
        self.label_btn.setToolTip("Turn this on, then click polygons on the map to put them in "
                                  "the selected class. Right-click removes a label.")
        self.label_btn.clicked.connect(self._toggle_label_tool)
        lay.addWidget(self.label_btn)

        self.assign_btn = QPushButton("…or assign the selected polygons")
        self.assign_btn.setToolTip("For bulk work: select polygons with QGIS's own select tool, "
                                   "then press this.")
        self.assign_btn.clicked.connect(self.assign_selected)
        lay.addWidget(self.assign_btn)

        # Selecting a polygon — with the label tool or QGIS's own select tool — shows what the
        # classifier thinks of it. Empty the rest of the time, so it costs no space until it has
        # something to say. This replaces a "fit" button (fitting is automatic) and a
        # jump-to-most-uncertain button (one polygon with no context was not much use).
        self.detail_lbl = QLabel("")
        self.detail_lbl.setTextFormat(_scoped(Qt, "TextFormat", "RichText"))
        self.detail_lbl.setWordWrap(True)
        lay.addWidget(self.detail_lbl)

        self.pause_box = QCheckBox("Pause the classifier — my edits only")
        self.pause_box.setToolTip(
            "Once the map is nearly right, stop it re-fitting. Every label you set then applies "
            "to that polygon and only that polygon, so correcting one thing can no longer "
            "reshuffle everything you already fixed. Your labels still accumulate, so you can "
            "still save them for another area.")
        self.pause_box.stateChanged.connect(self._toggle_pause)
        lay.addWidget(self.pause_box)

        self.guess_box = QCheckBox("Prefer a best guess over 'unknown'")
        self.guess_box.setToolTip(
            "Off: a class must clear its own confidence bar, so anything unfamiliar stays "
            "unknown. On: take the best-scoring class whenever it is over 50%. Far fewer "
            "unknowns, but it will also put genuinely new things into whichever class they "
            "happen to resemble most.")
        self.guess_box.stateChanged.connect(lambda _s: self._refit())
        lay.addWidget(self.guess_box)

        qrow = QHBoxLayout()
        qrow.addWidget(QLabel("Strictness"))
        self.q_slider = QSlider(_scoped(Qt, "Orientation", "Horizontal"))
        self.q_slider.setRange(0, 40)
        self.q_slider.setValue(5)
        self.q_slider.setToolTip("Higher demands a closer match, so more objects come back as "
                                 "unknown rather than being given a class they only half fit.")
        self.q_slider.sliderReleased.connect(self._refit)
        qrow.addWidget(self.q_slider)
        self.q_lbl = QLabel("0.05")
        self.q_slider.valueChanged.connect(lambda v: self.q_lbl.setText(f"{v/100:.2f}"))
        qrow.addWidget(self.q_lbl)
        lay.addLayout(qrow)

        srow = QHBoxLayout()
        self.save_btn = QPushButton("Save classes…")
        self.save_btn.setToolTip("Save the labelled examples so they can be reused on another "
                                 "area.")
        self.save_btn.clicked.connect(self.save_classes)
        srow.addWidget(self.save_btn)
        self.load_btn = QPushButton("Load classes…")
        self.load_btn.clicked.connect(self.load_classes)
        srow.addWidget(self.load_btn)
        lay.addLayout(srow)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.sync()

    def sync(self):
        ready = bool(self.host.vrt_path)
        has_polys = self._layer_ok()
        self.make_btn.setEnabled(ready)
        for w in (self.list, self.assign_btn, self.save_btn,
                  self.q_slider, self.label_btn, self.guess_box, self.pause_box):
            w.setEnabled(has_polys)
        if not ready:
            self.count_lbl.setText("Run a change map first.")

    # ---------------- polygons ----------------
    def make_polygons(self):
        try:
            import numpy as np
            from .engine import objects as OB
        except ImportError as exc:
            self.status.setText(f"Engine not importable: {exc}")
            return
        out_dir, vrt = self.host.out_dir, self.host.vrt_path
        if not vrt:
            return
        ya, yb = int(self.host.year_a.currentText()), int(self.host.year_b.currentText())
        self.count_lbl.setText("")

        # This runs on the UI thread on purpose: attaching vectors reprojects each polygon,
        # which is PROJ, and PROJ on a QGIS worker thread is what the whole job subprocess
        # exists to avoid. So QGIS does freeze — the dialog at least says what it is doing and
        # offers a cancel, instead of looking hung.
        dlg = QProgressDialog("Cutting polygons…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Make polygons")
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        QApplication.processEvents()

        def tick(done, total):
            if dlg.wasCanceled():
                return False
            dlg.setLabelText(f"Reading embeddings for object {done + 1} of {total}…")
            dlg.setValue(5 + int(90 * done / max(1, total)))
            QApplication.processEvents()
            return True

        try:
            polys, crs = OB.polygonize(vrt, self.host._threshold(), self.min_area.value())
            if not polys:
                dlg.close()
                self.status.setText("Nothing above the current cutoff. Lower it and retry.")
                return
            index = OB.CellIndex(out_dir, ya, yb)
            if not index:
                dlg.close()
                self.status.setText(
                    "No embeddings were captured for this job. Re-run the change map with "
                    "this version so the pooled grids are written alongside the tiles.")
                return
            dlg.setValue(5)
            vecs = OB.attach_vectors(polys, index, str(crs), progress=tick)
            if dlg.wasCanceled():
                dlg.close()
                self.status.setText("Cancelled.")
                return
            dlg.setLabelText("Building the layer…")
            dlg.setValue(96)
            QApplication.processEvents()
        except Exception as exc:
            dlg.close()
            self.status.setText(f"Could not make polygons: {exc}")
            return

        self._bank_labels()          # keep the examples, drop the row numbers
        self.polys, self.vectors = polys, np.asarray(vecs, dtype="float32")
        self._crs = str(crs)
        self._cut_threshold = self.host._threshold()
        self._build_layer(str(crs))
        self._save_objects()         # cutting these took minutes; never make it happen twice
        dlg.setValue(100)
        dlg.close()
        self.count_lbl.setText(
            f"{len(polys)} objects at cutoff {self.host._threshold():.2f}. "
            f"Add a class, turn on 'Label by clicking the map', and click a few.")
        self.status.setText("")
        self.sync()
        self._refit()

    def _build_layer(self, crs):
        self._remove_layer()
        layer = QgsVectorLayer(f"Polygon?crs={crs}", "change objects", "memory")
        pr = layer.dataProvider()
        # `idx` is the row of this polygon in self.vectors, and it is carried as a real
        # attribute rather than inferred from the feature id. The memory provider IGNORES
        # setId() and numbers features from 1, so indexing vectors by feature id would pair
        # every polygon with its neighbour's embedding and silently drop the last one — wrong
        # answers with nothing to notice.
        pr.addAttributes([QgsField("idx", _qv("int")),
                          QgsField("area_ha", _qv("float")),
                          QgsField("chg_mean", _qv("float")),
                          QgsField("chg_max", _qv("float")),
                          QgsField("label", _qv("str")),
                          QgsField("predicted", _qv("str")),
                          QgsField("confidence", _qv("float"))])
        layer.updateFields()
        feats = []
        for i, p in enumerate(self.polys):
            f = QgsFeature(layer.fields())
            f.setGeometry(QgsGeometry.fromWkt(p["wkt"]))
            f.setAttributes([i, p["area_ha"], p["chg_mean"], p["chg_max"], "", "", 0.0])
            feats.append(f)
        pr.addFeatures(feats)
        layer.updateExtents()
        # Into the host's group, so an area's polygons sit with the change map they came from
        # rather than loose at the top of the tree.
        add = getattr(getattr(self, "host", None), "_add_to_group", None)
        if add is not None:
            add(layer)
        else:
            QgsProject.instance().addMapLayer(layer)
        self.layer = layer
        # Built once. Resolving a feature's row by reading its attribute on every access meant
        # walking the layer several times per click, which is most of why labelling felt slow.
        idx_col = layer.fields().indexOf("idx")
        self._fid_row = {f.id(): int(f.attributes()[idx_col]) for f in layer.getFeatures()}
        # any selection — ours, or QGIS's own select tool — drives the breakdown readout
        layer.selectionChanged.connect(self._on_selection)
        self._style()

    def _layer_ok(self):
        """Is the polygon layer still alive?

        The user can remove it, or clear the project, at any time. The Python wrapper survives
        that but the C++ object does not, and touching it then raises RuntimeError from deep
        inside an unrelated call — which is exactly how this surfaced: 'wrapped C/C++ object of
        type QgsVectorLayer has been deleted', thrown from refreshing the class list.
        """
        if self.layer is None:
            return False
        try:
            lid = self.layer.id()
        except RuntimeError:
            self._forget_layer()
            return False
        if QgsProject.instance().mapLayer(lid) is None:
            self._forget_layer()
            return False
        return True

    def _forget_layer(self):
        """The polygons are gone, so anything indexed by their rows is meaningless. Banked
        class examples are NOT cleared — those are the user's labelling effort and they are
        stored as vectors, not row numbers, so they stay valid across areas."""
        self.layer = None
        self.polys = []
        self.vectors = None
        self.labels = {}
        self._fid_row = {}
        self.pred = None
        self.scores = None

    def detach(self):
        """Stop tracking the polygon layer WITHOUT removing it — it belongs to the previous
        area's group and that group should stay complete. Labels are banked first, so the
        training survives even though which polygon was which does not."""
        self._bank_labels()
        self._forget_layer()
        self.head = None
        self._refresh_list()
        self.sync()

    def _remove_layer(self):
        """Drop the map layer only. Deliberately NOT _forget_layer: this runs at the top of
        _build_layer, just before the new features are added from self.polys, so wiping the
        data here builds an empty layer every time."""
        if self.layer is not None:
            try:
                QgsProject.instance().removeMapLayer(self.layer.id())
            except Exception:
                pass
        self.layer = None
        self._fid_row = {}

    # ---------------- classes ----------------
    def add_class(self):
        name, ok = QInputDialog.getText(self, "Add class", "Name:")
        name = (name or "").strip()
        if not ok or not name or name in self.classes:
            return
        self.classes.append(name)
        self.colors[name] = _PALETTE[(len(self.classes) - 1) % len(_PALETTE)]
        self._refresh_list()
        self.list.setCurrentRow(len(self.classes) - 1)

    def rename_class(self):
        old = self.current_class()
        if not old:
            return
        name, ok = QInputDialog.getText(self, "Rename class", "Name:", text=old)
        name = (name or "").strip()
        if not ok or not name or name == old or name in self.classes:
            return
        self.classes[self.classes.index(old)] = name
        self.colors[name] = self.colors.pop(old, None)
        if old in self.class_vectors:
            self.class_vectors[name] = self.class_vectors.pop(old)
        self.labels = {k: (name if v == old else v) for k, v in self.labels.items()}
        self._refresh_list()
        self._refit()

    def delete_class(self):
        name = self.current_class()
        if not name:
            return
        self.classes.remove(name)
        self.colors.pop(name, None)
        self.class_vectors.pop(name, None)     # or it returns on the next fit
        self.labels = {k: v for k, v in self.labels.items() if v != name}
        self._refresh_list()
        self._refit()

    def current_class(self):
        item = self.list.currentItem()
        return item.data(_scoped(Qt, "ItemDataRole", "UserRole")) if item else None

    def _refresh_list(self):
        keep = self.current_class()
        self.list.clear()
        counts = {}
        for v in self.labels.values():
            counts[v] = counts.get(v, 0) + 1
        pred = self._predicted_counts()
        for name in self.classes:
            item = QListWidgetItem(
                f"  {name} — {counts.get(name, 0)} labelled, {pred.get(name, 0)} predicted")
            item.setData(_scoped(Qt, "ItemDataRole", "UserRole"), name)
            c = QColor(self.colors.get(name) or "#888780")
            item.setForeground(c)
            self.list.addItem(item)
        if pred.get(UNKNOWN):
            item = QListWidgetItem(f"  {UNKNOWN} — {pred[UNKNOWN]} predicted")
            item.setData(_scoped(Qt, "ItemDataRole", "UserRole"), None)
            self.list.addItem(item)
        for i in range(self.list.count()):
            if self.list.item(i).data(_scoped(Qt, "ItemDataRole", "UserRole")) == keep:
                self.list.setCurrentRow(i)
                break

    def _predicted_counts(self):
        """Counted from the prediction array, not by walking every feature. Rescanning the
        layer three times per click was a large part of why labelling took seconds."""
        out = {}
        if self.pred is None:
            return out
        for v in self.pred:
            v = str(v)
            if v:
                out[v] = out.get(v, 0) + 1
        return out

    # ---------------- labelling and fitting ----------------
    def assign_selected(self):
        name = self.current_class()
        if not name:
            self.status.setText("Pick a class first (or add one).")
            return
        if not self._layer_ok():
            self.status.setText("The polygon layer is gone — press 'Make polygons' again.")
            return
        rows = [self._row_of(f) for f in self.layer.selectedFeatures()]
        rows = [r for r in rows if r is not None]
        if not rows:
            self.status.setText("Select one or more polygons on the map first.")
            return
        for row in rows:
            self.labels[row] = name
        before = self.status.text()
        self._apply_manual(rows) if self.paused else self._refit()
        # never overwrite what _refit reported — that is where a real failure would show,
        # and hiding it behind a cheerful confirmation is how a broken fit looks like a
        # working one
        if self.status.text() == before:
            self.status.setText(f"{len(rows)} polygon(s) labelled '{name}'.")
        else:
            self.status.setText(f"{len(rows)} labelled '{name}'. {self.status.text()}")

    def q(self):
        return self.q_slider.value() / 100.0

    def _toggle_pause(self, _state=None):
        """Paused, the head stops learning and labels become direct edits.

        Refitting on every label is right while you are teaching it and wrong once you are
        finishing: at that point a correction that reshuffles twenty other polygons is undoing
        your work, not helping. The controls that would trigger a refit are disabled so it is
        obvious nothing is moving underneath you.
        """
        self.paused = self.pause_box.isChecked()
        for w in (self.q_slider, self.guess_box):
            w.setEnabled(not self.paused)
        if self.paused:
            self.status.setText("Paused. Labels now apply to just that polygon; nothing else "
                                "will move. Untick to let it learn from them.")
        else:
            self._refit()

    def _apply_manual(self, rows):
        """Write labels straight into the predictions, no fitting."""
        if self.pred is None:
            self.pred = np.array([""] * len(self.vectors), dtype=object)
        for row in rows:
            if 0 <= row < len(self.pred):
                self.pred[row] = self.labels.get(row, "")
        self._write_attrs(self.pred, None)
        self._style()
        self._refresh_list()
        self._show_selected()
        self._save_labels()          # paused labelling never reaches _refit
        self._report(self.pred)

    # ---------------- click to label ----------------
    def _toggle_label_tool(self):
        canvas = self.iface.mapCanvas()
        if canvas is None:
            return
        if not self.label_btn.isChecked():
            canvas.unsetMapTool(self.tool)
            return
        if not self.classes:
            self.label_btn.setChecked(False)
            self.status.setText("Add a class first, then click polygons to fill it.")
            return
        if self.tool is None:
            self.tool = LabelTool(canvas, self)
        canvas.setMapTool(self.tool)
        self.status.setText(f"Click polygons to label them '{self.current_class()}'. "
                            f"Right-click removes a label.")

    def label_at(self, point, clear=False):
        """Label whichever polygon was clicked. Hit-tests the polygon layer directly rather
        than relying on it being the active layer — the active layer is a QGIS concept the user
        should not have to think about here."""
        if not self._layer_ok():
            self.status.setText("The polygon layer is gone — press 'Make polygons' again.")
            return
        name = self.current_class()
        if name is None and not clear:
            self.status.setText("Pick a class in the list first.")
            return
        tol = self.iface.mapCanvas().mapUnitsPerPixel() * 3
        rect = QgsRectangle(point.x() - tol, point.y() - tol,
                            point.x() + tol, point.y() + tol)
        geom_pt = QgsGeometry.fromPointXY(QgsPointXY(point))
        hit = None
        for f in self.layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
            if f.geometry().intersects(geom_pt) or f.geometry().intersects(
                    QgsGeometry.fromRect(rect)):
                hit = f
                break
        if hit is None:
            self.status.setText("No polygon there.")
            return
        row = self._row_of(hit)
        if row is None:
            return
        if clear:
            self.labels.pop(row, None)
            note = "label removed"
        else:
            self.labels[row] = name
            note = f"labelled '{name}'"
        self.layer.selectByIds([hit.id()])          # flash it so the click is visibly registered
        self._apply_manual([row]) if self.paused else self._refit()
        self.status.setText(f"{note}. {self.status.text()}")

    def fit_and_classify(self):
        """Refit and reclassify everything, explicitly.

        Nothing in the UI calls this: fitting already runs on every label, class change and
        slider move, so a button for it was pure decoration. Kept as the named entry point for
        that operation.
        """
        if self.vectors is None:
            self.status.setText("Make polygons first.")
            return
        if not self._class_vectors():
            self.status.setText("Label at least one polygon first — pick a class, turn on "
                                "'Label by clicking the map', and click one.")
            return
        self._refit()
        if self.head is not None:
            n = len(self.head.classes)
            how = ("ranked by similarity to your examples" if self.head.single_class
                   else f"{n} classes, one detector each")
            n_lab = sum(len(v) for v in self._class_vectors().values())
            self.status.setText(f"Fitted on {n_lab} labelled object"
                                f"{'' if n_lab == 1 else 's'} ({how}). {self.status.text()}")

    # ---------------- what does it think of this one? ----------------
    _BAR = 13

    def _on_selection(self, *_a):
        self._show_selected()

    def _show_selected(self):
        """Show the per-class scores for the selected polygon.

        This is the thing that makes a wrong answer legible: a road cutting through a cutblock
        scores high on BOTH, and seeing that is what tells you the classes overlap rather than
        that the classifier is broken. Hidden entirely when nothing is selected.
        """
        if not self._layer_ok() or self.head is None or self.scores is None:
            self.detail_lbl.setText("")
            return
        sel = self.layer.selectedFeatures()
        if len(sel) != 1:
            self.detail_lbl.setText(
                f"<span style='color:gray'>{len(sel)} polygons selected</span>" if sel else "")
            return
        row = self._row_of(sel[0])
        if row is None:
            self.detail_lbl.setText("")
            return
        self.detail_lbl.setText(self._scores_html(row, sel[0]))

    def _scores_html(self, row, feat):
        col = self.scores[:, row]
        order = np.argsort(col)[::-1]
        kind = "similarity" if self.head.single_class else "confidence"
        lines = []
        for k in order:
            name = self.head.classes[int(k)]
            v = float(col[k])
            fires = v >= self.head.thr.get(name, 1.0)
            filled = max(0, min(self._BAR, int(round(v * self._BAR))))
            bar = "█" * filled + "·" * (self._BAR - filled)
            c = QColor(self.colors.get(name) or "#888780")
            hexc = c.name() if fires else "#8a8a8a"
            weight = "600" if fires else "400"
            lines.append(
                f"<span style='color:{hexc};font-weight:{weight}'>"
                f"{bar}</span>&nbsp;<span style='color:{hexc}'>{v:.2f}&nbsp;{name}</span>")
        verdict = str(self.pred[row]) if self.pred is not None else ""
        mine = self.labels.get(row)
        head = (f"<b>{feat['area_ha']:.1f} ha</b> · change {feat['chg_mean']:.3f} · "
                f"{'you labelled this <b>' + mine + '</b>' if mine else 'called <b>' + verdict + '</b>'}")
        return (f"<div style='font-size:11px'>{head}</div>"
                f"<div style='font-family:Consolas,monospace;font-size:11px;line-height:150%'>"
                + "<br>".join(lines) +
                f"</div><div style='font-size:10px;color:gray'>{kind}; "
                f"bold cleared its own bar</div>")

    def _select_class_on_map(self, item):
        """Double-clicking a class row selects its polygons — including 'unknown', which is the
        review list: everything the classifier could not account for, all at once."""
        if not self._layer_ok() or self.pred is None:
            return
        name = item.data(_scoped(Qt, "ItemDataRole", "UserRole")) or UNKNOWN
        ids = [fid for fid, r in self._fid_row.items()
               if 0 <= r < len(self.pred) and str(self.pred[r]) == name]
        self.layer.selectByIds(ids)
        self.status.setText(f"{len(ids)} polygon(s) predicted '{name}' selected.")

    def _row_of(self, feature):
        """Which row of self.vectors this feature is. Never the feature id — the memory
        provider ignores setId() and numbers from 1 (see _build_layer)."""
        row = self._fid_row.get(feature.id())
        if row is None:
            try:
                row = int(feature["idx"])
            except (KeyError, TypeError, ValueError):
                return None
        return row if self.vectors is not None and 0 <= row < len(self.vectors) else None

    def _write_attrs(self, pred=None, conf=None):
        """Write labels (and optionally predictions) in ONE provider call.

        The previous version opened an edit session and called changeAttributeValue per feature
        per field, three times per click. Each commit is a provider round trip and a full
        repaint, which is what made a single click take seconds on a few hundred polygons.
        """
        if not self._layer_ok():
            return
        fields = self.layer.fields()
        li, pi, ci = (fields.indexOf("label"), fields.indexOf("predicted"),
                      fields.indexOf("confidence"))
        updates = {}
        for fid, row in self._fid_row.items():
            attrs = {li: self.labels.get(row, "")}
            if pred is None:
                attrs[pi] = ""            # no model: nothing has an opinion, say so
                attrs[ci] = 0.0
            elif 0 <= row < len(pred):
                attrs[pi] = str(pred[row])
                attrs[ci] = float(conf[row]) if conf is not None else 0.0
            updates[fid] = attrs
        if updates:
            self.layer.dataProvider().changeAttributeValues(updates)
            self.layer.triggerRepaint()

    def _class_vectors(self):
        """Every example of every class: those banked from earlier polygon sets plus those
        labelled on the current one. Banked examples are stored as VECTORS, so they stay
        meaningful after a re-polygonize or a move to a different area."""
        out = {k: list(v) for k, v in self.class_vectors.items() if len(v)}
        if self.vectors is not None:
            for row, name in self.labels.items():
                if 0 <= row < len(self.vectors):
                    out.setdefault(name, []).append(self.vectors[row])
        return {k: v for k, v in out.items() if v}

    def restore(self):
        """Reload a run's polygons and labelling progress from its folder. Returns a message,
        or None if there was nothing saved.

        Called after the dock reconnects to a saved run. Predictions are NOT read back — they
        are refit from the restored classes, which reproduces them exactly and keeps the
        GeoPackage immutable.
        """
        paths = self._store_paths()
        if not paths:
            return None
        gpkg, jsn = paths
        from .engine import store as ST
        msg = []
        if os.path.exists(gpkg):
            try:
                polys, vecs, crs = ST.load_objects(gpkg)
            except Exception as exc:
                return f"Could not read saved polygons: {exc}"
            if polys:
                self.polys, self.vectors, self._crs = polys, vecs, crs
                self.labels = {}
                self._build_layer(str(crs))
                msg.append(f"{len(polys)} polygons")
        if os.path.exists(jsn):
            try:
                cv, colors, labels, thr, names = ST.load_labels(jsn)
            except Exception as exc:
                return f"Could not read saved labels: {exc}"
            self.class_vectors = {k: list(v) for k, v in cv.items()}
            self.colors.update(colors or {})
            for name in names:
                if name not in self.classes:
                    self.classes.append(name)
            # Labels are keyed to the polygons in the GeoPackage, which are LOADED, not re-cut —
            # so they always line up and there is nothing to guard against. (An earlier version
            # compared the saved cut against the live slider, which a freshly opened dock always
            # fails, so labels were silently never restored.)
            self.labels = dict(labels) if self.polys else {}
            self._cut_threshold = thr
            if thr is not None:
                # Put the slider back where these polygons were cut, so the symbology and the
                # objects on screen agree.
                self.host.slider.setValue(int(round(thr * 100)))
            n_ex = sum(len(v) for v in self.class_vectors.values()) + len(self.labels)
            msg.append(f"{len(self.classes)} classes / {n_ex} examples")
        if not msg:
            return None
        self._refresh_list()
        self.sync()
        self._refit(force=True)
        return "Restored " + ", ".join(msg) + "."

    def _store_paths(self):
        """(objects gpkg, labels json) for the CURRENT run, or None in temp mode.

        Only a run with a real output folder persists. Temp runs deliberately do not: the folder
        is deleted on unload, so writing there would just be a slower way to lose it.
        """
        out = getattr(self.host, "out_dir", None)
        if not out or not os.path.isdir(out):
            return None
        # Temp mode is defined by living under the session temp root, not by whether a text box
        # looks empty. The folder is deleted on unload, so writing there is a slower way to
        # lose the work.
        tmp_root = getattr(self.host, "_tmp_root", None)
        if tmp_root and os.path.abspath(out).startswith(os.path.abspath(tmp_root)):
            return None
        ya, yb = int(self.host.year_a.currentText()), int(self.host.year_b.currentText())
        from .engine import store as ST
        return ST.objects_path(out, ya, yb), ST.labels_path(out, ya, yb)

    def _save_objects(self):
        """Written ONCE per cut. Never rewritten — labels live in the JSON precisely so an
        800-row GeoPackage is not rebuilt on every click."""
        paths = self._store_paths()
        if not paths or self.vectors is None or not self.polys:
            return
        from .engine import store as ST
        try:
            ST.save_objects(paths[0], self.polys, self.vectors, self._crs)
        except Exception as exc:
            self.status.setText(f"Polygons not saved: {exc}")

    def _save_labels(self):
        """A few KB, so this can run on every label without anyone noticing."""
        paths = self._store_paths()
        if not paths:
            return
        from .engine import store as ST
        try:
            # The CUT threshold, not whatever the slider says now. Moving the slider without
            # re-polygonizing changes the symbology, not the objects, and recording the live
            # value would make a reopened run claim a cut it was never made at.
            thr = self._cut_threshold
            ST.save_labels(paths[1], self.class_vectors, self.colors, self.labels,
                           self.host._threshold() if thr is None else thr, names=self.classes)
        except Exception:
            pass                     # never let a failed autosave interrupt labelling

    def _bank_labels(self):
        """Move the current set's labels into the banked examples, before the rows they point
        at cease to exist."""
        if self.vectors is None:
            return
        for row, name in self.labels.items():
            if 0 <= row < len(self.vectors):
                self.class_vectors.setdefault(name, []).append(self.vectors[row])
        self.labels = {}

    def _refit(self, force=False):
        """Refit and reclassify. Cheap enough (a few hundred vectors) to run on every label.

        ONE class is a perfectly good starting point — it means "find me more like these" — so
        this must not wait for a second class before doing anything. The head handles the
        single-class case by ranking similarity instead of discriminating.
        """
        if not self._layer_ok() or self.vectors is None or (self.paused and not force):
            return
        try:
            from .engine import head as H
        except ImportError:
            return
        classes = self._class_vectors()
        if not classes:
            self.head = None
            self._clear_predictions()
            self._refresh_list()
            self._save_labels()
            self.status.setText("Add a class, then click polygons on the map to label them.")
            return
        try:
            # the pool is every candidate: with a single class the cut in a similarity ranking
            # can only be found from the candidates, not from the handful of labelled examples
            self.head = H.fit_from_classes(
                classes, pool=self.vectors, abstain_quantile=self.q(),
                decision="argmax" if self.guess_box.isChecked() else "threshold")
            pred, self.scores = self.head.predict(self.vectors)
            conf = self.head.confidence(self.vectors)
            pred = np.asarray(pred, dtype=object)
            # A correction the user made is never revised by the model. This is the last thing
            # to touch `pred`, on the one path that produces it, so no refit — from a new class,
            # the strictness slider, the best-guess toggle or a loaded preset — can walk over an
            # answer the user already gave.
            for row, name in self.labels.items():
                if 0 <= row < len(pred):
                    pred[row] = name
            self.pred = pred
        except Exception as exc:
            self.status.setText(f"Could not fit: {exc}")
            return
        self._write_attrs(pred, conf)
        self._style()
        self._refresh_list()
        self._show_selected()
        self._report(pred)
        # Every path that changes a label or a class ends here, so this one call covers all of
        # them — clicking the map, assigning a selection, renaming, deleting, loading a preset.
        self._save_labels()

    def _report(self, pred):
        counts = {}
        for v in pred:
            counts[str(v)] = counts.get(str(v), 0) + 1
        n_unknown = counts.pop(UNKNOWN, 0)
        parts = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        mode = " (ranked by similarity)" if self.head and self.head.single_class else ""
        self.status.setText(
            f"{parts or 'nothing classified'}{mode}"
            + (f" · {n_unknown} to review" if n_unknown else ""))

    def _clear_predictions(self):
        self.pred = None
        self.scores = None
        self._write_attrs()
        self._style()

    def _symbol(self, fill, outline, width="0.6", style="solid"):
        """A FRESH symbol every time. QgsRendererCategory takes ownership of the symbol it is
        given, so handing the same object to two categories leaves two C++ owners of one
        pointer. That is a heap corruption (0xC0000374), and it does not fire when the renderer
        is built — it fires when the old renderer is destroyed, i.e. the next time the layer is
        restyled. Never share, never reuse.

        Built explicitly rather than from defaultSymbol(), whose hairline outline is almost
        invisible over imagery."""
        return QgsFillSymbol.createSimple({
            "color": fill, "outline_color": outline, "outline_width": width,
            "outline_style": style})

    def _style(self):
        """Colour by prediction. Unknown stays a hollow outline — it is an honest answer, not a
        class, and it should not compete visually with the ones the user defined."""
        if not self._layer_ok():
            return
        cats = []
        for name in self.classes:
            c = QColor(self.colors.get(name) or "#888780")
            rgb = f"{c.red()},{c.green()},{c.blue()}"
            cats.append(QgsRendererCategory(
                name, self._symbol(f"{rgb},110", f"{rgb},255", "0.8"), name))
        # The two no-answer cases mean different things and must not look alike:
        #   unknown = the classifier ran and refused to guess (a real answer)
        #   ""      = nothing has been fitted yet, so it has no opinion at all
        # Both get a faint fill as well as an outline. Hollow hairlines were nearly invisible
        # over imagery, and "not classified yet" is the ONLY thing on screen before the first
        # class exists — the state a new user starts in should not look like an empty map.
        cats.append(QgsRendererCategory(
            UNKNOWN, self._symbol("255,255,255,30", "255,255,255,255", "1.0"),
            "unknown — no confident match"))
        cats.append(QgsRendererCategory(
            "", self._symbol("255,255,255,22", "235,235,235,220", "0.7", "dash"),
            "not classified yet"))
        try:
            self.layer.setRenderer(QgsCategorizedSymbolRenderer("predicted", cats))
            self.layer.triggerRepaint()
        except Exception:
            pass

    # ---------------- presets ----------------
    def save_classes(self):
        if not self._class_vectors():
            self.status.setText("Nothing labelled yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save classes", "", "JSON (*.json)")
        if not path:
            return
        try:
            from .engine import head as H
            H.save_classes(path, self._class_vectors(), self.colors)
            self.status.setText(f"Saved to {os.path.basename(path)}.")
        except Exception as exc:
            self.status.setText(f"Save failed: {exc}")

    def load_classes(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load classes", "", "JSON (*.json)")
        if not path:
            return
        try:
            from .engine import head as H
            classes, colors = H.load_classes(path)
        except Exception as exc:
            self.status.setText(f"Load failed: {exc}")
            return
        if self.vectors is not None and classes:
            width = len(next(iter(classes.values()))[0])
            if width != self.vectors.shape[1]:
                QMessageBox.warning(self, "Classes don't fit",
                                    "These classes were saved from a different embedding "
                                    "layout and can't be applied here.")
                return
        self.classes = list(classes)
        self.colors.update({k: v for k, v in (colors or {}).items() if v})
        for i, name in enumerate(self.classes):
            self.colors.setdefault(name, _PALETTE[i % len(_PALETTE)])
        self.class_vectors = {k: list(v) for k, v in classes.items()}
        # Go through the ONE fitting path rather than predicting here. An earlier version
        # duplicated it and, in doing so, quietly dropped three things the real path does:
        # it re-applied nothing over the predictions (so loading a preset overwrote the user's
        # own corrections), it left self.pred stale (so the class counts disagreed with the
        # map), and it ignored the strictness and best-guess settings.
        # force=True because loading a preset is an explicit "classify with this" instruction,
        # which should not silently do nothing just because the head is paused.
        self._refit(force=True)
        n_lab = sum(len(v) for v in classes.values())
        self.status.setText(
            f"Loaded {len(self.classes)} classes ({n_lab} examples). {self.status.text()}")

    def cleanup(self):
        if self.tool is not None and self.iface.mapCanvas() is not None:
            self.iface.mapCanvas().unsetMapTool(self.tool)
        self.tool = None
        self._remove_layer()
