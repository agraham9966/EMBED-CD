"""The 'what happened here' half of the dock: polygons, labels, and the editable head.

Lives in its own file because the dock is already the big one, and this is a self-contained
panel: give it a finished change job and it owns everything from there.

Labelling is click-a-polygon-on-the-map, via LabelTool. An earlier version made the user drive
QGIS's own selection first; that was a bad call — it silently depends on the polygon layer being
the active one, and gives no feedback when it isn't. Selection is still there as a bulk path.
"""
import os

import numpy as np

from qgis.PyQt.QtCore import Qt, QSize, QTimer
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QDoubleSpinBox, QInputDialog, QFileDialog, QMessageBox, QSlider, QCheckBox,
    QProgressDialog, QApplication, QComboBox, QToolButton, QMenu, QWidgetAction,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsFillSymbol,
    QgsFeatureRequest, QgsRectangle, QgsPointXY,
)
from qgis.gui import QgsMapTool

UNKNOWN = "unknown"
# (what the user sees, what the head is told). Order matters: the first is the default.
_MODES = (("Transition — what changed", "delta"),
          ("End state — what it is now", "after"))
_ARROW = " → "          # what a Transition class name is built with, and split back on
_MODE_KEYS = [k for _label, k in _MODES]
# (key, label). Keyed, because the "class" entry is RENAMED live to whatever class is selected
# and matching a filter on what it currently says would break the moment it says something else.
_CYCLES = (("uncertain", "Least certain first"),
           ("unlabelled", "Only unlabelled"),
           ("unknown", "Only unknown"),
           ("labelled", "Only labelled"),
           ("class", "Only the selected class"),
           ("all", "All objects"))
# The panel opens on End state while the ENGINE still defaults to delta — deliberately two
# different defaults. The engine's is a data-compatibility contract: a labels file or preset
# written before modes existed records no mode, and delta is what it was in fact labelled
# under, so reading it any other way would silently change what it means. The panel's is a
# usability choice for new work, and carries no such history.
_MODE_SETTING = "embed_cd/classify_features"
_DEFAULT_MODE = "after"
_PALETTE = ["#d85a30", "#1d9e75", "#7f77dd", "#d4537e", "#378add", "#ba7517",
            "#639922", "#993556"]


class _ClassList(QListWidget):
    """A class list whose colour swatch is clickable.

    setItemWidget on every row would give the same thing but replaces the item with a widget,
    which costs the list's own selection and keyboard behaviour. Reading the click x against the
    icon width keeps a plain QListWidget and everything that comes with it.
    """

    def __init__(self, on_swatch):
        super().__init__()
        self._on_swatch = on_swatch

    def mousePressEvent(self, ev):
        item = self.itemAt(ev.pos())
        x = int(ev.position().x()) if hasattr(ev, "position") else int(ev.x())
        super().mousePressEvent(ev)
        if item is not None and x <= self.iconSize().width() + 6:
            self._on_swatch(item)


def row_of(*widgets):
    """Small helper: a horizontal strip, since the options panel needs a couple of them."""
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(4)
    for x in widgets:
        l.addWidget(x)
    return w


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
    """Click a polygon on the map: to label it, or just to ask what the model makes of it.

    This exists because making the user drive QGIS's selection first was a bad call: it depends
    on the right layer being active, gives no feedback when it isn't, and is an extra concept
    for something that should be one click.

    One tool for both jobs rather than two, because they are the same gesture on the same
    layer and only the consequence differs — and because two QgsMapTools would have to be kept
    mutually exclusive by hand anyway.
    """

    def __init__(self, canvas, panel):
        super().__init__(canvas)
        self.panel = panel

    def canvasReleaseEvent(self, event):
        pt = self.toMapCoordinates(event.pos())
        if self.panel.inspecting():
            self.panel.inspect_at(pt)
            return
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
        # Undo is a stack of BATCHES, not single labels: "assign the selected polygons" can set
        # thirty at once and unwinding those one press at a time would be useless. Each entry is
        # [(row, label_before_or_None), ...]. Scoped to the current polygon set and dropped when
        # it changes — after a re-cut the labels have become free-floating class vectors with no
        # rows to put them back into, so there is nothing coherent to undo TO.
        self._undo = []
        self._cycle = []               # ordered rows for the < > arrows
        self._filling_combo = False    # writing TO the class combo, not reading a user edit
        self._band = None              # yellow highlight over the current object
        self._current_row = None       # the object being stepped through
        self._styling = False          # guards the styleChanged read-back loop
        self._cycle_at = -1
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
        self.make_btn = QPushButton("Generate Embedded Vector Set")
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

        self.list = _ClassList(self._swatch_clicked)
        self.list.setIconSize(QSize(16, 16))
        self.list.setMaximumHeight(120)
        self.list.setToolTip("Click to pick the class you are labelling with. "
                             "Double-click to select that class's polygons on the map — "
                             "double-click 'unknown' to see everything still unaccounted for.")
        self.list.itemDoubleClicked.connect(self._select_class_on_map)
        self.list.currentRowChanged.connect(lambda _r: self._class_selection_changed())
        lay.addWidget(self.list)

        # Three full-width buttons for class housekeeping became a strip of small ones, which
        # is how QGIS's own layer and style panels handle exactly this. The gear at the end
        # holds everything that is real but rarely touched mid-session.
        crow = QHBoxLayout()
        crow.setSpacing(3)
        # QGIS's own icons rather than typed glyphs: a "+" in a label is whatever the UI font
        # happens to draw, which is why they looked low-res next to real toolbar buttons.
        from qgis.core import QgsApplication as _QApp
        for icon, text, slot, tip in (
                ("/symbologyAdd.svg", "Add", self.add_class, "Add a class"),
                ("/symbologyRemove.svg", "", self.delete_class, "Delete the selected class"),
                ("/mActionEditTable.svg", "", self.rename_class,
                 "Rename the selected class")):
            b = QToolButton()
            ic = _QApp.getThemeIcon(icon)
            if not ic.isNull():
                b.setIcon(ic)
                b.setIconSize(QSize(18, 18))
            if text and ic.isNull():
                b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setFixedSize(QSize(28, 26))
            b.clicked.connect(slot)
            crow.addWidget(b)
        self.color_btn = QToolButton()
        _ic = _QApp.getThemeIcon("/mIconColorBox.svg")
        if not _ic.isNull():
            self.color_btn.setIcon(_ic)
            self.color_btn.setIconSize(QSize(18, 18))
        else:
            self.color_btn.setText("■")
        self.color_btn.setToolTip("Change the selected class's colour.")
        self.color_btn.setAutoRaise(True)
        self.color_btn.setFixedSize(QSize(28, 26))
        self.color_btn.clicked.connect(self.pick_color)
        crow.addWidget(self.color_btn)

        # Reading what the model thinks used to require LABELLING something: the breakdown
        # below only appeared after a click that also assigned a class. So the only way to ask
        # "why did you call it that" was to overwrite the answer you were asking about.
        self.inspect_btn = QToolButton()
        _ii = _QApp.getThemeIcon("/mActionIdentify.svg")
        if not _ii.isNull():
            self.inspect_btn.setIcon(_ii)
            self.inspect_btn.setIconSize(QSize(18, 18))
        else:
            self.inspect_btn.setText("?")
        self.inspect_btn.setCheckable(True)
        self.inspect_btn.setToolTip(
            "Inspect: click a polygon to see the model's scores for it, without labelling it."
            + chr(10) * 2 +
            "The same click while 'Label by clicking the map' is on assigns a class instead, so "
            "only one of the two is ever active.")
        self.inspect_btn.setAutoRaise(True)
        self.inspect_btn.setFixedSize(QSize(28, 26))
        self.inspect_btn.clicked.connect(self._toggle_inspect)
        crow.addWidget(self.inspect_btn)
        crow.addStretch(1)
        self.opts_btn = QToolButton()
        self.opts_btn.setText("⚙")
        self.opts_btn.setAutoRaise(True)
        self.opts_btn.setFixedWidth(26)
        self.opts_btn.setToolTip("Classifier options: bulk assign, pause, best guess, "
                                 "strictness.")
        self.opts_btn.setPopupMode(_scoped(QToolButton, "ToolButtonPopupMode", "InstantPopup"))
        crow.addWidget(self.opts_btn)
        # Saving gets its own button rather than hiding inside the gear: a class set is the
        # user's own work and the fact that it CAN be carried to another area is the whole
        # point of the classifier. Buried in a settings menu, nobody finds it.
        self.savemenu_btn = QToolButton()
        self.savemenu_btn.setText("💾")
        self.savemenu_btn.setAutoRaise(True)
        self.savemenu_btn.setFixedWidth(26)
        self.savemenu_btn.setToolTip("Save or load a class set, to reuse it on another area.")
        self.savemenu_btn.setPopupMode(
            _scoped(QToolButton, "ToolButtonPopupMode", "InstantPopup"))
        crow.addWidget(self.savemenu_btn)
        lay.addLayout(crow)

        # Built here, shown in the gear menu (see below). It was a visible row for one version
        # and that was one row too many — with the label button, the class stepper, the review
        # row and the breakdown, the panel had become a stack of controls with no shape. It is
        # a decision you make once per session, not per object, so it belongs with the other
        # once-per-session settings.
        mrow = QHBoxLayout()
        mrow.setSpacing(4)
        mrow.addWidget(QLabel("Classify by:"))
        self.mode_combo = QComboBox()
        for label, key in _MODES:
            self.mode_combo.addItem(label, key)
        _i = self.mode_combo.findData(self._remembered_mode())
        if _i > 0:
            self.mode_combo.setCurrentIndex(_i)     # before the signal below is connected
        self.mode_combo.setToolTip(
            "What a class means." + chr(10) * 2 +
            "Transition — what happened here: 'forest → clearing'. The object's before AND "
            "after are both used, so a class is tied to the land cover it started from. This "
            "is the default and it is the better answer when you care how something changed."
            + chr(10) * 2 +
            "End state — what it is now, ignoring what preceded it. Use this to carry classes "
            "to a different landscape: measured, a class trained on one baseline is recognised "
            "on a baseline it never saw 99% of the time, against 0% for Transition. The cost "
            "is that it can no longer tell 'became bare' from 'was already bare'." + chr(10) * 2 +
            "Switching is free and reversible — both read the same stored embeddings, so "
            "nothing is recomputed and no labels are lost.")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        mrow.addWidget(self.mode_combo, 1)
        self.mode_row = QWidget()
        self.mode_row.setLayout(mrow)
        mrow.setContentsMargins(0, 0, 0, 0)

        self.label_btn = QPushButton("Label by clicking the map")
        self.label_btn.setCheckable(True)
        self.label_btn.setToolTip("Turn this on, then click polygons on the map to put them in "
                                  "the selected class. Right-click removes a label.")
        self.label_btn.clicked.connect(self._toggle_label_tool)
        lay.addWidget(self.label_btn)

        self.assign_btn = QPushButton("Assign the selected polygons to this class")
        self.assign_btn.setToolTip("For bulk work: select polygons with QGIS's own select tool, "
                                   "then press this.")
        self.assign_btn.clicked.connect(self.assign_selected)

        # Two rows, grouped by what they DO. They used to be grouped by nothing in
        # particular: the arrows sat with the object's class editor while Undo and Discard sat
        # with the filter that drives the arrows — the two halves swapped. That is most of why
        # the dropdowns read as an interchangeable pair when they are completely different
        # kinds of thing (one is a filter, one is a value).
        #
        # Row 1 is navigation: the filter and the arrows it drives.
        rrow = QHBoxLayout()
        rrow.setSpacing(3)
        rrow.addWidget(QLabel("Step through:"))
        self.cycle_mode = QComboBox()
        for _key, _label in _CYCLES:
            self.cycle_mode.addItem(_label, _key)
        self.cycle_mode.setToolTip(
            "What the arrows step through." + chr(10) * 2 +
            "Least certain first: the ones it would not commit to, then the ones it nearly "
            "called differently — where a label teaches the model most. Skips what you have "
            "already labelled; use 'Only labelled' to revisit those." + chr(10) +
            "Only unlabelled: everything you have not personally labelled, whatever the model "
            "guessed for it." + chr(10) +
            "Only unknown: the ones the model would not commit to." + chr(10) +
            "Only labelled: check what you have already assigned." + chr(10) +
            "Only \"<class>\": the class selected in the list above — both the ones you put "
            "there and the ones the model did.")
        self.cycle_mode.currentIndexChanged.connect(
            lambda _i: setattr(self, "_cycle_at", -1))
        rrow.addWidget(self.cycle_mode, 1)
        self.prev_btn = QPushButton("◀")
        self.prev_btn.setFixedWidth(30)
        self.next_btn = QPushButton("▶")
        self.next_btn.setFixedWidth(30)
        for b, d in ((self.prev_btn, -1), (self.next_btn, 1)):
            b.setToolTip("Step to the previous/next object in the list on the left. The map "
                         "pans to it and keeps your zoom; the breakdown below shows what the "
                         "classifier makes of it.")
            b.clicked.connect(lambda _c, dd=d: self.step(dd))
            rrow.addWidget(b)
        lay.addLayout(rrow)

        # Row 2 is the current object: what it is, and the two things you do to it.
        r2 = QHBoxLayout()
        r2.setSpacing(3)
        r2.addWidget(QLabel("This object:"))
        self.cls_combo = QComboBox()
        self.cls_combo.setToolTip(
            "The class of the object you are looking at. Changing it relabels that object — "
            "the same as clicking it on the map, without leaving the keyboard.")
        self.cls_combo.currentIndexChanged.connect(self._class_combo_changed)
        r2.addWidget(self.cls_combo, 1)
        self.undo_btn = QPushButton("↶ Undo")
        self.undo_btn.setToolTip(
            "Undo the last labelling action. A bulk assign comes back as one step, not thirty."
            + chr(10) * 2 +
            "An unlabelled object returns to the classifier's own answer rather than going "
            "blank, so the colour may not change.")
        self.undo_btn.clicked.connect(self.undo_label)
        r2.addWidget(self.undo_btn)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setToolTip("Remove this object's label.")
        self.discard_btn.clicked.connect(self.discard_label)
        r2.addWidget(self.discard_btn)
        lay.addLayout(r2)

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

        self.guess_box = QCheckBox("Prefer a best guess over 'unknown'")
        self.guess_box.setToolTip(
            "Off: a class must clear its own confidence bar and look familiar, so anything "
            "that does neither stays unknown." + chr(10) * 2 +
            "On: every object gets its best-scoring class and nothing is ever unknown. Useful "
            "for a first pass or a map with no gaps — but it will also file genuinely new "
            "things under whichever class they resemble most, so the classes stop meaning "
            "what they say.")
        self.guess_box.stateChanged.connect(lambda _s: self._refit())

        qrow = QHBoxLayout()
        qrow.addWidget(QLabel("Strictness"))
        self.q_slider = QSlider(_scoped(Qt, "Orientation", "Horizontal"))
        self.q_slider.setRange(0, 40)
        self.q_slider.setValue(5)
        self.q_slider.setToolTip(
            "How sure a class has to be before it claims an object. Higher leaves more "
            "objects unknown rather than giving them a class they only half fit." + chr(10) * 2 +
            "With ONE class it moves the cut in a similarity ranking. With several it raises "
            "every class's confidence bar, roughly 0.5 at the left to 0.9 at the right." +
            chr(10) * 2 +
            "No effect while 'Prefer a best guess' is on — that mode never abstains.")
        self.q_slider.sliderReleased.connect(self._refit)
        qrow.addWidget(self.q_slider)
        self.q_lbl = QLabel("0.05")
        self.q_slider.valueChanged.connect(lambda v: self.q_lbl.setText(f"{v/100:.2f}"))
        qrow.addWidget(self.q_lbl)

        self.save_btn = QPushButton("Save classes…")
        self.save_btn.setToolTip("Save the labelled examples so they can be reused on another "
                                 "area.")
        self.save_btn.clicked.connect(self.save_classes)
        self.load_btn = QPushButton("Load classes…")
        self.load_btn.clicked.connect(self.load_classes)

        # Everything above is real and none of it is touched more than once or twice a session,
        # so it lives behind the gear rather than competing with the controls used every few
        # seconds. QWidgetAction keeps the SAME widgets — nothing is re-implemented for the
        # menu, so every existing signal, tooltip and test still applies.
        menu = QMenu(self)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(8, 6, 8, 6)
        pl.setSpacing(6)
        pl.addWidget(self.mode_row)
        pl.addWidget(self.assign_btn)
        pl.addWidget(self.pause_box)
        pl.addWidget(self.guess_box)
        qwrap = QWidget()
        qwrap.setLayout(qrow)
        pl.addWidget(qwrap)
        act = QWidgetAction(menu)
        act.setDefaultWidget(panel)
        menu.addAction(act)
        self.opts_btn.setMenu(menu)
        self._opts_menu = menu

        smenu = QMenu(self)
        spanel = QWidget()
        sl = QVBoxLayout(spanel)
        sl.setContentsMargins(8, 6, 8, 6)
        sl.setSpacing(6)
        hint = QLabel("A class set is just your labelled examples — save it and the same "
                      "classes can be applied to another area.")
        hint.setWordWrap(True)
        hint.setMaximumWidth(240)
        hint.setStyleSheet("color: palette(mid);")
        sl.addWidget(hint)
        sl.addWidget(self.save_btn)
        sl.addWidget(self.load_btn)
        sact = QWidgetAction(smenu)
        sact.setDefaultWidget(spanel)
        smenu.addAction(sact)
        self.savemenu_btn.setMenu(smenu)
        self._save_menu = smenu

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.sync()

    def sync(self):
        ready = bool(self.host.vrt_path)
        has_polys = self._layer_ok()
        self.make_btn.setEnabled(ready)
        for w in (self.list, self.assign_btn, self.save_btn,
                  self.q_slider, self.label_btn, self.guess_box, self.pause_box,
                  self.prev_btn, self.next_btn, self.cycle_mode, self.discard_btn,
                  self.mode_combo, self.inspect_btn):
            w.setEnabled(has_polys)
        self._sync_review()
        # A restored run already HAS objects, so telling the user to run one is stale advice —
        # the label has to reflect what is loaded, not only what this session did.
        if not ready:
            self.count_lbl.setText("Run a change map first.")
        elif has_polys and self.polys:
            n_lab = len(self.labels) + sum(len(v) for v in self.class_vectors.values())
            self.count_lbl.setText(f"{len(self.polys)} objects · {n_lab} labelled")
        elif ready:
            self.count_lbl.setText("Press 'Generate Embedded Vector Set' to cut the changed area into objects, "
                "each carrying its own embedding.")

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
        # The polygons are now the answer; the raster underneath just competes with the outlines.
        hide = getattr(self.host, "show_change_raster", None)
        if hide is not None:
            hide(False)
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
        # Two-way: recolouring a class in QGIS's symbology dialog edits this same layer, and
        # without listening the panel would keep showing the old colour and then overwrite it
        # on the next refit.
        try:
            layer.styleChanged.connect(self._colors_from_layer)
        except Exception:
            pass
        self._style()

    def _highlight(self, geom=None):
        """Draw the yellow outline of the current object as a canvas overlay.

        A rubber band rather than QGIS's selection rendering, because selection replaces the
        feature's own symbol and there is only one selection symbol per layer — so it could
        never keep each object's class colour underneath. As an overlay the class fill shows
        through and the yellow only ever adds an outline.
        """
        from qgis.gui import QgsRubberBand, QgsMapCanvas
        from qgis.core import QgsWkbTypes

        canvas = self.iface.mapCanvas()
        # QgsRubberBand takes a QgsMapCanvas*, and sip hands anything else straight to C++ —
        # a stand-in canvas does not raise, it takes the process down. Check the real type
        # rather than trusting the caller.
        if not isinstance(canvas, QgsMapCanvas):
            return
        if getattr(self, "_band", None) is None:
            try:
                self._band = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
            except Exception:
                self._band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
            self._band.setColor(QColor(255, 215, 0))
            self._band.setFillColor(QColor(0, 0, 0, 0))       # outline only
            self._band.setWidth(3)
        if geom is None:
            self._band.reset(_scoped(QgsWkbTypes, "GeometryType", "PolygonGeometry")
                             if hasattr(QgsWkbTypes, "GeometryType") else
                             QgsWkbTypes.PolygonGeometry)
            return
        self._band.setToGeometry(geom, self.layer)

    def _clear_highlight(self):
        if getattr(self, "_band", None) is not None:
            self._highlight(None)

    def _style_selection(self, layer):
        """Selected objects get a yellow OUTLINE, not QGIS's default solid yellow fill.

        Stepping through objects selects each one, and a solid fill hides the very thing you are
        being asked to judge — the change underneath. An outline says "this one" without
        covering it. Older QGIS has no selection-symbol API, in which case the default stands;
        nothing here is worth an exception.
        """
        try:
            from qgis.core import QgsFillSymbol, Qgis
            # A selection symbol REPLACES the feature's own rendering, and there is only one per
            # layer — so any fill here would erase the class colour and you could no longer tell
            # what an object is without reading the dropdown. Hence no fill and no stroke at all:
            # selection changes nothing, and the yellow outline is drawn separately as a
            # highlight over the top, where it can sit on the class colour instead of replacing
            # it.
            invisible = QgsFillSymbol.createSimple({
                "color": "0,0,0,0", "outline_color": "0,0,0,0", "outline_width": "0"})
            props = layer.selectionProperties()
            # The enum lives on Qgis, and the properties class is
            # QgsVectorLayerSelectionProperties — there is no QgsSelectionProperties to import.
            # Getting that wrong threw inside the try and the bare except swallowed it, so the
            # default solid yellow simply stayed and the failure was invisible.
            props.setSelectionRenderingMode(Qgis.SelectionRenderingMode.CustomSymbol)
            props.setSelectionSymbol(invisible)
        except Exception as exc:
            self.status.setText(f"(selection style unavailable: {exc})")

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
        self._clear_highlight()
        self._current_row = None
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
    def features(self):
        """Which question the classes answer: "delta" (transition) or "after" (end state)."""
        if getattr(self, "mode_combo", None) is None:
            return _DEFAULT_MODE
        return self.mode_combo.currentData() or _DEFAULT_MODE

    @staticmethod
    def _remembered_mode():
        """The mode this user last chose, or the default.

        Per-user rather than per-project: which question you ask is a working habit, and being
        put back into the other one every time QGIS restarts is the kind of small friction that
        makes a control feel broken. A run SAVED in a mode still overrides this when it is
        reopened — that file knows what its own class names meant. This only seeds new work.
        """
        try:
            from qgis.core import QgsSettings
            val = QgsSettings().value(_MODE_SETTING, _DEFAULT_MODE)
        except Exception:
            return _DEFAULT_MODE
        return val if val in _MODE_KEYS else _DEFAULT_MODE

    def _remember_mode(self):
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue(_MODE_SETTING, self.features())
        except Exception:
            pass

    def _set_features(self, key):
        """Point the combo at a mode without treating it as a user edit."""
        if getattr(self, "mode_combo", None) is None:
            return
        i = self.mode_combo.findData(key)
        if i < 0 or i == self.mode_combo.currentIndex():
            return
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(i)
        self.mode_combo.blockSignals(False)

    def _mode_changed(self, _i):
        """force=True: choosing a mode is an explicit "classify with this", the same reasoning
        load_classes uses. Without it, switching while paused would appear to do nothing."""
        self._remember_mode()
        self._refit(force=True)
        self._save_labels()
        self.status.setText(
            f"Now classifying by {self.mode_combo.currentText().split(chr(0x2014))[0].strip()}. "
            "Your labels are unchanged — the same examples, read differently.")

    def _ask_class_name(self, title, text=""):
        """Name a class, prompting for the transition when that is what a class means.

        In End state mode this is one box, exactly as it always was. In Transition mode it is
        two, because the NAME is the only place a transition is recorded — nothing in the data
        model stores a before and an after, and it should not: the name is already displayed in
        the class list, the legend, two GeoPackage columns, the score breakdown and the status
        line, and a structured pair would need formatting at every one of them.

        Filling only the first box gives exactly the old behaviour, so the second is an offer
        and never an obstacle.
        """
        from qgis.PyQt.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QLineEdit

        if self.features() != "delta":
            name, ok = QInputDialog.getText(self, title, "Name:", text=text)
            return (name or "").strip() if ok else None

        before, _, after = text.partition(_ARROW.strip()) if _ARROW.strip() in text             else (text, "", "")
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)
        e_from, e_to = QLineEdit(before.strip()), QLineEdit(after.strip())
        e_from.setPlaceholderText("forest")
        e_to.setPlaceholderText("clearing   (optional)")
        e_to.setToolTip("Leave this empty to use the first box alone as the name.")
        form.addRow("From:", e_from)
        form.addRow("To:", e_to)
        hint = QLabel("A class is a transition, so name both ends. Leave 'To' empty for a "
                      "plain name.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        form.addRow(hint)
        buttons = QDialogButtonBox(
            _scoped(QDialogButtonBox, "StandardButton", "Ok")
            | _scoped(QDialogButtonBox, "StandardButton", "Cancel"))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        e_from.setFocus()
        if dlg.exec() != _scoped(QDialog, "DialogCode", "Accepted"):
            return None
        a, b = e_from.text().strip(), e_to.text().strip()
        return f"{a}{_ARROW}{b}" if a and b else (a or b)

    def add_class(self):
        name = self._ask_class_name("Add class")
        if not name or name in self.classes:
            return
        self.classes.append(name)
        self.colors[name] = _PALETTE[(len(self.classes) - 1) % len(_PALETTE)]
        self._refresh_list()
        self.list.setCurrentRow(len(self.classes) - 1)

    def rename_class(self):
        old = self.current_class()
        if not old:
            return
        name = self._ask_class_name("Rename class", text=old)
        if not name or name == old or name in self.classes:
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

    @staticmethod
    def _swatch(hexcol):
        """A solid colour chip. Filled directly rather than painted: QPainter on a QPixmap needs
        a GUI-enabled application, and this runs under one that may not be."""
        from qgis.PyQt.QtGui import QPixmap
        pm = QPixmap(14, 14)
        pm.fill(QColor(hexcol))
        return QIcon(pm)

    def _swatch_clicked(self, item):
        name = item.data(_scoped(Qt, "ItemDataRole", "UserRole"))
        if name:
            self.pick_color(name)

    def pick_color(self, name=None):
        """Change a class's colour, here and on the map.

        The layer is the same object QGIS's own symbology dialog edits, so writing the renderer
        is what makes this two-way: our change shows there, and a change made there comes back
        through `styleChanged`.
        """
        from qgis.PyQt.QtWidgets import QColorDialog
        name = name if isinstance(name, str) and name else self.current_class()
        if not name:
            self.status.setText("Pick a class in the list first.")
            return
        start = QColor(self.colors.get(name) or "#888780")
        col = QColorDialog.getColor(start, self, f"Colour for '{name}'")
        if not col.isValid():
            return
        self.colors[name] = col.name()
        self._style()
        self._refresh_list()

    def _colors_from_layer(self):
        """Read class colours back off the renderer.

        Editing the layer's symbology in QGIS is a perfectly reasonable way to recolour a class,
        and without this the panel would keep showing — and then re-apply — the old colour.
        """
        if self._styling or not self._layer_ok():
            return
        r = self.layer.renderer()
        if not hasattr(r, "categories"):
            return
        changed = False
        for cat in r.categories():
            name = str(cat.value())
            if name in self.classes and cat.symbol() is not None:
                hexcol = cat.symbol().color().name()
                if self.colors.get(name) != hexcol:
                    self.colors[name] = hexcol
                    changed = True
        if changed:
            self._refresh_list()

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
                f" {name} — {counts.get(name, 0)} labelled, {pred.get(name, 0)} predicted")
            item.setData(_scoped(Qt, "ItemDataRole", "UserRole"), name)
            item.setIcon(self._swatch(self.colors.get(name) or "#888780"))
            item.setToolTip("Click the colour box to change this class's colour.")
            self.list.addItem(item)
        if pred.get(UNKNOWN):
            item = QListWidgetItem(f"  {UNKNOWN} — {pred[UNKNOWN]} predicted")
            item.setData(_scoped(Qt, "ItemDataRole", "UserRole"), None)
            self.list.addItem(item)
        for i in range(self.list.count()):
            if self.list.item(i).data(_scoped(Qt, "ItemDataRole", "UserRole")) == keep:
                self.list.setCurrentRow(i)
                break
        self._sync_cycle_class_entry()      # add / rename / delete all land here

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

    # ---------------- undo / review ----------------
    def _push_undo(self, rows):
        """Snapshot these rows' labels BEFORE they change."""
        self._undo.append([(r, self.labels.get(r)) for r in rows])
        del self._undo[:-100]           # a session's worth; the stack is not the point
        self._sync_review()

    def undo_label(self):
        if not self._undo:
            self.status.setText("Nothing to undo.")
            return
        batch = self._undo.pop()
        for row, before in batch:
            if before is None:
                self.labels.pop(row, None)
            else:
                self.labels[row] = before
        rows = [r for r, _ in batch]
        self._apply_manual(rows) if self.paused else self._refit()
        # Undo restores the LABEL, and a polygon with no label falls back to the model's
        # prediction rather than going blank — labels override predictions, they do not replace
        # them. So the colour may not change, which is correct but worth saying.
        self.status.setText(f"Undid {len(batch)} label{'s' if len(batch) != 1 else ''}. "
                            "Unlabelled objects go back to the classifier's own answer.")
        if rows:
            self._goto_row(rows[0])

    def _cycle_rows(self):
        """The ordered rows the arrows walk, per the filter.

        Keyed on `currentData()`, never on the visible text: one entry is renamed live to
        whatever class is selected, so matching on what it says would break the moment it says
        something else.
        """
        combo = getattr(self, "cycle_mode", None)
        mode = combo.currentData() if combo is not None else "all"
        n = 0 if self.vectors is None else len(self.vectors)
        if mode == "uncertain":
            if self.head is None or self.scores is None or self.pred is None:
                return list(range(n))
            from .engine import head as H
            # `locked` marks what the user has already answered. review_order has accepted this
            # parameter since the port and NOTHING passed it, so the work-list kept handing back
            # objects that were already settled — which is what made stepping feel like it was
            # wandering between classes at random. Those rows are still reachable, under
            # "Only labelled", which exists for exactly that.
            locked = np.zeros(n, bool)
            for r in self.labels:
                if 0 <= r < n:
                    locked[r] = True
            return [int(i) for i in H.review_order(self.pred, self.scores, locked=locked)]
        if mode == "unlabelled":
            # Not the same as "unknown": the model may be perfectly confident about an object
            # you have never confirmed. This is the "what still needs my eyes" list.
            return [i for i in range(n) if i not in self.labels]
        if mode == "unknown":
            if self.pred is None:
                return list(range(n))
            return [i for i in range(n)
                    if str(self.pred[i]) in (UNKNOWN, "") and i not in self.labels]
        if mode == "labelled":
            return sorted(self.labels)
        if mode == "class":
            name = self.current_class()
            if not name:
                return []
            # Both what YOU put there and what the MODEL put there. Walking a class is how you
            # check its predictions at least as much as your own labels, and a filter that
            # showed only your own would be a list you already know the contents of.
            return [i for i in range(n)
                    if self.labels.get(i) == name
                    or (i not in self.labels and self.pred is not None and i < len(self.pred)
                        and str(self.pred[i]) == name)]
        return list(range(n))

    def _sync_cycle_class_entry(self):
        """Name the class filter after the class that is selected.

        The connection between picking a class and stepping through it should be visible in the
        control, not something to be discovered — reading `Only "cutblock"` says what will
        happen; reading `Only the selected class` does not say which.
        """
        combo = getattr(self, "cycle_mode", None)
        if combo is None:
            return
        i = combo.findData("class")
        if i < 0:
            return
        name = self.current_class()
        combo.setItemText(i, f'Only "{name}"' if name else "Only the selected class")
        # A filter that can only walk nothing should not be pickable. Guarded because this
        # relies on the combo's default QStandardItemModel.
        model = combo.model()
        item = model.item(i) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(bool(name))

    def _class_selection_changed(self):
        """Picking a class changes what the arrows would walk, so the cursor into that list is
        no longer meaningful."""
        self._show_selected()
        self._sync_cycle_class_entry()
        self._cycle_at = -1

    def step(self, delta):
        rows = self._cycle_rows()
        if not rows:
            self.status.setText("Nothing to step through with this filter.")
            return
        if self._cycle_at < 0 or self._cycle_at >= len(rows):
            self._cycle_at = 0 if delta > 0 else len(rows) - 1
        else:
            self._cycle_at = (self._cycle_at + delta) % len(rows)
        self._goto_row(rows[self._cycle_at])
        self.status.setText(f"{self._cycle_at + 1} of {len(rows)}  "
                            f"({self.cycle_mode.currentText().lower()})")

    def _goto_row(self, row):
        """Select the polygon and PAN to it, keeping the current scale.

        Deliberately not zoom-to-feature: stepping through hundreds of objects while the scale
        jumps to fit each one is disorienting, and the surrounding context is most of what tells
        you whether a label is right. Only zooms out if the object does not fit as things are.
        """
        if not self._layer_ok():
            return
        fid = next((f for f, r in self._fid_row.items() if r == row), None)
        if fid is None:
            return
        # NOT selectByIds. QGIS renders a selected feature with the layer's single selection
        # symbol INSTEAD of its own, so any selection at all loses the class colour — measured:
        # default paints it yellow, and an "invisible" selection symbol paints nothing at all.
        # The current object is our own idea, so we track it and draw it ourselves.
        self._current_row = row
        feat = next((f for f in self.layer.getFeatures() if f.id() == fid), None)
        if feat is None:
            return
        self._highlight(feat.geometry())
        self._show_selected()
        self._sync_class_combo()
        box = feat.geometry().boundingBox()
        canvas = self.iface.mapCanvas()
        try:
            from qgis.core import QgsCoordinateTransform, QgsProject
            tr = QgsCoordinateTransform(self.layer.crs(), canvas.mapSettings().destinationCrs(),
                                        QgsProject.instance())
            box = tr.transformBoundingBox(box)
        except Exception:
            pass
        cur = canvas.extent()
        if box.width() > cur.width() or box.height() > cur.height():
            box.scale(1.4)
            canvas.setExtent(box)
        else:
            keep = QgsRectangle(cur)
            keep.setXMinimum(box.center().x() - cur.width() / 2)
            keep.setXMaximum(box.center().x() + cur.width() / 2)
            keep.setYMinimum(box.center().y() - cur.height() / 2)
            keep.setYMaximum(box.center().y() + cur.height() / 2)
            canvas.setExtent(keep)
        canvas.refresh()

    def discard_label(self):
        """Remove the selected polygon's label. Right-click on the map does this too, but while
        arrow-stepping you are not necessarily clicking the map."""
        if not self._layer_ok():
            return
        # The current object first — stepping no longer selects, so reading the QGIS selection
        # here would always come back empty. A real selection still works for bulk removal.
        rows = [r for r in (self._row_of(f) for f in self.layer.selectedFeatures())
                if r is not None]
        cur = self._selected_row()
        if not rows and cur is not None:
            rows = [cur]
        rows = [r for r in rows if r in self.labels]
        if not rows:
            self.status.setText("No labelled polygon selected.")
            return
        self._push_undo(rows)
        for r in rows:
            self.labels.pop(r, None)
        self._apply_manual(rows) if self.paused else self._refit()
        self.status.setText(f"Removed {len(rows)} label{'s' if len(rows) != 1 else ''}.")

    def _selected_row(self):
        """The object the panel is talking about: the one being stepped through, or failing
        that a single feature the user picked with QGIS's own select tool."""
        if not self._layer_ok():
            return None
        row = getattr(self, "_current_row", None)
        if row is not None and self.vectors is not None and 0 <= row < len(self.vectors):
            return row
        sel = self.layer.selectedFeatures()
        return self._row_of(sel[0]) if len(sel) == 1 else None

    def _class_combo_changed(self, _i):
        """Setting the combo relabels the selected object.

        Guarded by `_filling_combo` because the combo is also written TO whenever the selection
        moves; without that, panning onto an object would immediately relabel it as whatever it
        already was — harmless-looking, but it would fill the undo stack with phantom edits and
        make every stepped-past object count as user-labelled.
        """
        if self._filling_combo:
            return
        row = self._selected_row()
        if row is None:
            return
        name = self.cls_combo.currentData()
        if name == self.labels.get(row):
            return
        self._push_undo([row])
        if name is None:
            self.labels.pop(row, None)
        else:
            self.labels[row] = name
        self._apply_manual([row]) if self.paused else self._refit()

    def _sync_class_combo(self):
        """Show the selected object's class: its label if it has one, otherwise the model's
        answer, marked as a guess so the two are never confused."""
        if getattr(self, "cls_combo", None) is None:
            return
        row = self._selected_row()
        self._filling_combo = True
        try:
            self.cls_combo.clear()
            self.cls_combo.addItem("— no label —", None)
            for name in self.classes:
                self.cls_combo.addItem(name, name)
            self.cls_combo.setEnabled(row is not None)
            if row is None:
                self.cls_combo.setItemText(0, "— no object selected —")
                return
            mine = self.labels.get(row)
            if mine is None and self.pred is not None and 0 <= row < len(self.pred):
                guess = str(self.pred[row])
                self.cls_combo.setItemText(
                    0, f"— unlabelled ({guess or 'unknown'} predicted) —")
            i = self.cls_combo.findData(mine)
            self.cls_combo.setCurrentIndex(max(0, i))
        finally:
            self._filling_combo = False

    def _sync_review(self):
        if getattr(self, "undo_btn", None) is not None:
            self.undo_btn.setEnabled(bool(self._undo))
        self._sync_class_combo()

    # ---------------- labelling and fitting ----------------
    def assign_selected(self):
        name = self.current_class()
        if not name:
            self.status.setText("Pick a class first (or add one).")
            return
        if not self._layer_ok():
            self.status.setText("The polygon layer is gone — press 'Generate Embedded Vector Set' again.")
            return
        rows = [self._row_of(f) for f in self.layer.selectedFeatures()]
        rows = [r for r in rows if r is not None]
        if not rows:
            self.status.setText("Select one or more polygons on the map first.")
            return
        self._push_undo(rows)           # one batch: undo must take all thirty back together
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
        for w in (self.q_slider, self.guess_box, self.mode_combo):
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
        self.inspect_btn.setChecked(False)          # one click, one consequence
        self._ensure_tool()
        self.status.setText(f"Click polygons to label them '{self.current_class()}'. "
                            f"Right-click removes a label.")

    def inspecting(self):
        return bool(getattr(self, "inspect_btn", None)) and self.inspect_btn.isChecked()

    def _ensure_tool(self):
        canvas = self.iface.mapCanvas()
        if canvas is None:
            return
        if self.tool is None:
            self.tool = LabelTool(canvas, self)
        canvas.setMapTool(self.tool)

    def _toggle_inspect(self):
        """Inspect and Label are the same click with different consequences, so exactly one of
        them is ever on."""
        if self.inspect_btn.isChecked():
            self.label_btn.setChecked(False)
            self._ensure_tool()
            self.status.setText("Click a polygon to see what the classifier makes of it. "
                                "Nothing is labelled in this mode.")
        else:
            canvas = self.iface.mapCanvas()
            if canvas is not None and self.tool is not None:
                canvas.unsetMapTool(self.tool)
            self.status.setText("")

    def _polygon_at(self, point):
        """The feature under a map click, or None.

        Hit-tests the polygon layer directly rather than relying on it being the active layer —
        the active layer is a QGIS concept the user should not have to think about here.

        The click arrives in the CANVAS's CRS and `setFilterRect` wants the LAYER's, and until
        0.36 those were always the same thing, so nothing converted between them. Then the
        change map moved to the area's UTM zone while projects stay in Web Mercator, and the
        search rectangle started landing 14,000 km from the polygon it was aimed at. Every
        click found nothing and labelling silently stopped working.
        """
        canvas = self.iface.mapCanvas()
        tol = canvas.mapUnitsPerPixel() * 3
        rect = QgsRectangle(point.x() - tol, point.y() - tol,
                            point.x() + tol, point.y() + tol)
        point = QgsPointXY(point)
        try:
            src = canvas.mapSettings().destinationCrs()
            dst = self.layer.crs()
            if src.isValid() and dst.isValid() and src != dst:
                from qgis.core import QgsCoordinateTransform
                tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
                # The RECTANGLE is transformed, not rebuilt from a tolerance, so the click
                # tolerance stays three screen pixels rather than three of whatever unit the
                # target CRS happens to use.
                rect = tr.transformBoundingBox(rect)
                point = tr.transform(point)
        except Exception:
            pass                    # no canvas CRS to speak of (tests): treat them as the same
        geom_pt = QgsGeometry.fromPointXY(point)
        for f in self.layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
            if f.geometry().intersects(geom_pt) or f.geometry().intersects(
                    QgsGeometry.fromRect(rect)):
                return f
        return None

    def inspect_at(self, point):
        """Show the breakdown for a clicked polygon, and change nothing.

        Reading the model's reasoning used to require LABELLING: the breakdown only appeared
        after a click that also assigned a class, so the only way to ask "why did you call it
        that" was to overwrite the answer being asked about.

        Deliberately not `_goto_row`, which pans to centre the object — right when an arrow key
        brought you to something off-screen, wrong when you just clicked the thing in front of
        you.
        """
        if not self._layer_ok():
            return
        hit = self._polygon_at(point)
        if hit is None:
            self.status.setText("No polygon there.")
            return
        row = self._row_of(hit)
        if row is None:
            return
        self._current_row = row
        self._highlight(hit.geometry())
        self._show_selected()
        self._sync_class_combo()
        mine = self.labels.get(row)
        called = str(self.pred[row]) if self.pred is not None and row < len(self.pred) else ""
        self.status.setText(
            f"you labelled this '{mine}'." if mine else
            f"the model calls this '{called or 'nothing yet'}'.")

    def label_at(self, point, clear=False):
        """Label whichever polygon was clicked."""
        if not self._layer_ok():
            self.status.setText("The polygon layer is gone — press 'Generate Embedded Vector Set' again.")
            return
        name = self.current_class()
        if name is None and not clear:
            self.status.setText("Pick a class in the list first.")
            return
        hit = self._polygon_at(point)
        if hit is None:
            self.status.setText("No polygon there.")
            return
        row = self._row_of(hit)
        if row is None:
            return
        self._push_undo([row])
        if clear:
            self.labels.pop(row, None)
            note = "label removed"
        else:
            self.labels[row] = name
            note = f"labelled '{name}'"
        self._current_row = row                     # highlight it so the click is visibly registered
        self._highlight(hit.geometry())
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
        self._sync_class_combo()
        if self._layer_ok():
            sel = self.layer.selectedFeatures()
            self._highlight(sel[0].geometry() if len(sel) == 1 else None)

    def _show_selected(self):
        """Show the per-class scores for the selected polygon.

        This is the thing that makes a wrong answer legible: a road cutting through a cutblock
        scores high on BOTH, and seeing that is what tells you the classes overlap rather than
        that the classifier is broken. Hidden entirely when nothing is selected.
        """
        if not self._layer_ok() or self.head is None or self.scores is None:
            self._set_detail("")
            return
        row = self._selected_row()
        if row is None:
            sel = self.layer.selectedFeatures()
            self._set_detail(
                f"<span style='color:gray'>{len(sel)} polygons selected</span>" if sel else "")
            return
        sel = [f for f in self.layer.getFeatures() if self._row_of(f) == row]
        if not sel:
            self._set_detail("")
            return
        self._set_detail(self._scores_html(row, sel[0]))

    def _set_detail(self, html):
        """Write the breakdown, then re-fit the step that holds it.

        The breakdown grows a row per class, so with five classes it is three lines taller than
        it was with two. The dock sizes each accordion step to the height its page needed when
        the step was last fitted and turns the inner scrollbars OFF, so anything that grows
        afterwards is simply clipped — the lowest-scoring classes vanish with nothing to say
        they were there. Re-fit on the next event loop turn, once the label has laid itself out
        at its new height.
        """
        self.detail_lbl.setText(html)
        fit = getattr(self.host, "_fit_steps", None)
        if fit is not None:
            QTimer.singleShot(0, fit)

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
                cv, colors, labels, thr, names, feats = ST.load_labels(jsn)
            except Exception as exc:
                return f"Could not read saved labels: {exc}"
            self.class_vectors = {k: list(v) for k, v in cv.items()}
            # Before the refit at the end, so the run comes back answering the question it was
            # left answering. The names were chosen under that reading.
            self._set_features(feats)
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
        # Temp runs save too, and deliberately. The folder is deleted on unload so nothing
        # survives the session — but WITHIN a session it is what lets you switch to another
        # area and come back to find your objects and labels still there. Without it, moving
        # between two temp runs silently destroys the one you left.
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
                           self.host._threshold() if thr is None else thr, names=self.classes,
                           features=self.features())
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
        # The rows these referred to are about to stop existing, so the stack cannot restore
        # anything meaningful. Dropping it is the honest option; keeping it would offer an undo
        # that silently mislabels whatever now sits at those indices.
        self._undo = []
        self._cycle_at = -1

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
                decision="argmax" if self.guess_box.isChecked() else "threshold",
                features=self.features())
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
        self._sync_review()
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
        sym = QgsFillSymbol.createSimple({
            "color": fill, "outline_color": outline, "outline_width": width,
            "outline_style": style})
        # A label the USER set and a label the model guessed look identical otherwise, which is
        # the one distinction that matters while reviewing. Driven off the `label` attribute per
        # feature, so one symbol per class still covers both cases.
        try:
            from qgis.core import QgsProperty, QgsSymbolLayer
            # DASHED for what the user labelled, solid for what the model predicted, everything
            # else identical. A heavier line was the first attempt and it read as noise — at
            # this width the difference between 0.6 and 1.6 mm is not something the eye picks
            # out across a map, whereas solid-versus-dashed is unmistakable at any width.
            sl = sym.symbolLayer(0)
            sl.setDataDefinedProperty(
                QgsSymbolLayer.Property.StrokeStyle,
                QgsProperty.fromExpression(
                    "CASE WHEN \"label\" IS NOT NULL AND \"label\" <> '' "
                    "THEN 'dash' ELSE 'solid' END"))
        except Exception:
            pass
        return sym

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
        # `styleChanged` fires on setRenderer, and the handler reads colours back off the
        # renderer — so without this flag our own write would immediately re-enter and rebuild
        # the list on every restyle.
        self._styling = True
        try:
            self.layer.setRenderer(QgsCategorizedSymbolRenderer("predicted", cats))
            self.layer.triggerRepaint()
        except Exception:
            pass
        finally:
            self._styling = False

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
            H.save_classes(path, self._class_vectors(), self.colors, features=self.features())
            self.status.setText(f"Saved to {os.path.basename(path)}.")
        except Exception as exc:
            self.status.setText(f"Save failed: {exc}")

    def load_classes(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load classes", "", "JSON (*.json)")
        if not path:
            return
        try:
            from .engine import head as H
            classes, colors, feats = H.load_classes(path)
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
        # Adopt the preset's mode. The vectors load fine either way — that is deliberate — but
        # the NAMES were written under one reading, and "forest -> clearing" read as an end
        # state is a class that no longer says what it means. Switching and saying so beats
        # silently reinterpreting someone's labelling.
        was = self.features()
        self._set_features(feats)
        switched = self.features() != was
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
        note = (f" Switched to {self.mode_combo.currentText().split(chr(0x2014))[0].strip()}, "
                "which is how these were labelled." if switched else "")
        self.status.setText(
            f"Loaded {len(self.classes)} classes ({n_lab} examples).{note} {self.status.text()}")

    def cleanup(self):
        if self.tool is not None and self.iface.mapCanvas() is not None:
            self.iface.mapCanvas().unsetMapTool(self.tool)
        self.tool = None
        self._remove_layer()
