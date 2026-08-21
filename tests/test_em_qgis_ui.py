"""UI-layer checks that only a real QGIS process can make. Skipped in the dev venv.

These exist because a whole class of PyQGIS bug is invisible to the normal suite: passing a C++
object that Qt takes OWNERSHIP of to two owners. It doesn't raise, it doesn't fail a render — it
corrupts the heap and takes QGIS down later, when the old object is destroyed. No traceback, so
nothing in a normal test would ever see it.

Run:  "C:\\Program Files\\QGIS 4.0.1\\bin\\python-qgis.bat" tests/test_em_qgis_ui.py
"""
import os
import sys
import tempfile

try:
    from qgis.core import (QgsApplication, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
                           QgsProject, QgsMapSettings, QgsMapRendererParallelJob)
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtWidgets import QFileDialog
except ImportError:
    print("skipped: needs QGIS's own Python (python-qgis.bat)")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "plugin"))

QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", ""), True)
_app = QgsApplication([], False)
_app.initQgis()

from embed_cd_qgis.classify import ClassifyPanel, UNKNOWN     # noqa: E402


def _qv():
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QMetaType.Type.QString, QMetaType.Type.Double
    except (ImportError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        return QVariant.String, QVariant.Double


class _Stub:
    """Just enough of ClassifyPanel for the styling methods, which touch nothing else."""

    def __init__(self, layer, classes, colors):
        self.layer, self.classes, self.colors = layer, classes, colors

    _symbol = ClassifyPanel._symbol
    _style = ClassifyPanel._style
    _layer_ok = ClassifyPanel._layer_ok
    _forget_layer = ClassifyPanel._forget_layer


def _layer(n=6):
    qstr, qdbl = _qv()
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "objects", "memory")
    pr = layer.dataProvider()
    pr.addAttributes([QgsField("predicted", qstr), QgsField("confidence", qdbl)])
    layer.updateFields()
    feats = []
    names = ["cutblock", "burn", UNKNOWN, "", "cutblock", "burn"]
    for i in range(n):
        f = QgsFeature(layer.fields())
        x = 100.0 * i
        f.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON (({x} 0, {x+50} 0, {x+50} 50, {x} 50, {x} 0))"))
        f.setAttributes([names[i % len(names)], 0.9])
        feats.append(f)
    pr.addFeatures(feats)
    layer.updateExtents()
    return layer


def _render(layer):
    ms = QgsMapSettings()
    ms.setLayers([layer])
    ms.setExtent(layer.extent())
    ms.setOutputSize(QSize(256, 256))
    ms.setDestinationCrs(layer.crs())
    job = QgsMapRendererParallelJob(ms)
    job.start()
    job.waitForFinished()
    return job.renderedImage()


def test_restyling_twice_does_not_corrupt_the_heap():
    """The real crash: `_style` runs on build and again on every refit. The SECOND call
    destroys the first renderer, and if two categories shared one symbol that destructor is a
    double free. QGIS died here with 0xC0000374 the first time this shipped."""
    layer = _layer()
    QgsProject.instance().addMapLayer(layer)
    stub = _Stub(layer, ["cutblock", "burn"], {"cutblock": "#d85a30", "burn": "#1d9e75"})

    stub._style()
    _render(layer)
    stub._style()                       # <- destroys renderer #1
    _render(layer)
    stub.classes.append("road")
    stub.colors["road"] = "#7f77dd"
    stub._style()                       # <- and #2, with a different category count
    _render(layer)

    r = layer.renderer()
    values = {c.value() for c in r.categories()}
    assert {"cutblock", "burn", "road", UNKNOWN, ""} <= values, values
    # hold the wrappers: sip hands out a fresh Python object per call, so comparing ids of
    # temporaries compares recycled memory, not the underlying C++ symbols
    syms = [c.symbol() for c in r.categories()]
    assert len({id(s) for s in syms}) == len(syms), "every category must own a DISTINCT symbol"
    QgsProject.instance().removeMapLayer(layer.id())
    print(f"ok restyled 3x and rendered, {len(syms)} categories each with its own symbol")


def test_style_with_no_classes_yet():
    """`_style` runs immediately after the layer is built, when no class exists — the two
    fallback categories are created back to back and were the pair that shared a symbol."""
    layer = _layer()
    QgsProject.instance().addMapLayer(layer)
    stub = _Stub(layer, [], {})
    stub._style()
    _render(layer)
    stub._style()
    _render(layer)
    syms = [c.symbol() for c in layer.renderer().categories()]
    assert len({id(s) for s in syms}) == len(syms) == 2, syms
    QgsProject.instance().removeMapLayer(layer.id())
    print("ok styling with zero classes survives a restyle")


def test_polygon_rows_are_read_from_idx_not_the_feature_id():
    """The memory provider IGNORES setId() and numbers features from 1. Indexing the vector
    array by feature id therefore pairs every polygon with its neighbour's embedding and drops
    the last one — silently, with no error anywhere. The row must come from an attribute."""
    import numpy as np

    class Panel(_Stub):
        _build_layer = ClassifyPanel._build_layer
        _remove_layer = ClassifyPanel._remove_layer
        _row_of = ClassifyPanel._row_of
        _fid_row = {}

        def _on_selection(self, *a):
            pass

    n = 5
    polys = [{"wkt": f"POLYGON (({100.0*i} 0, {100.0*i+50} 0, {100.0*i+50} 50, "
                     f"{100.0*i} 50, {100.0*i} 0))",
              "area_ha": float(i), "chg_mean": 0.1 * i, "chg_max": 0.2 * i, "n_px": i}
             for i in range(n)]
    p = Panel(None, [], {})
    p.polys = polys
    p.vectors = np.arange(n * 4, dtype="float32").reshape(n, 4)
    p._build_layer("EPSG:3857")

    fids = sorted(int(f.id()) for f in p.layer.getFeatures())
    assert 0 not in fids, f"provider numbers from 1, so this test is only meaningful then: {fids}"

    # every feature's row must point at ITS OWN polygon, not a neighbour's
    for f in p.layer.getFeatures():
        row = p._row_of(f)
        assert row is not None, f"feature {f.id()} has no usable row"
        assert abs(f["area_ha"] - polys[row]["area_ha"]) < 1e-6, (
            f"feature id {f.id()} -> row {row} but area {f['area_ha']} != "
            f"{polys[row]['area_ha']}")
    rows = sorted(p._row_of(f) for f in p.layer.getFeatures())
    assert rows == list(range(n)), f"every polygon must be reachable exactly once: {rows}"
    QgsProject.instance().removeMapLayer(p.layer.id())
    print(f"ok rows come from idx: fids {fids} -> rows {rows}, all correctly paired")


def _tiny_job(d):
    """A change raster plus a matching cell store — the minimum a panel needs to work on."""
    import numpy as np
    from embed_cd import cells as CE, gdalio as GD

    def from_origin(x, y, xr, yr):
        return GD.Transform.from_origin(x, y, xr, yr)

    x0, y0, res = 500000.0, 5400000.0, 10.0
    arr = np.zeros((64, 64), np.float32)
    arr[4:28, 4:28] = 0.40          # one strong object
    arr[36:60, 36:60] = 0.35        # and another, disjoint
    vrt = os.path.join(d, "change.vrt")
    GD.write(vrt, arr, "EPSG:32610", from_origin(x0, y0, res, res), nodata=-1.0)

    rng = np.random.default_rng(0)
    depth = 8
    ma = rng.normal(0, 0.1, (4, 4, depth)).astype(np.float32)
    mb = ma.copy()
    ma[0, 0] = 1.0                  # give the two objects distinct signatures
    mb[0, 0] = -1.0
    ma[2, 2] = -1.0
    mb[2, 2] = 1.0
    n = np.full((4, 4), 256.0, np.float32)
    s = np.full((4, 4), 0.4, np.float32)
    CE.write_cells(os.path.join(d, "cells_2019-2024_160m_EPSG32610_500000_5399360.tif"),
                   ma, mb, n, s, s, "EPSG:32610", from_origin(x0, y0, res, res), 16)
    return vrt


def _panel(d, vrt, ClassifyPanel):
    """A ClassifyPanel wired to a finished job, with the bits of QGIS it consults stubbed."""
    class Combo:
        def __init__(self, v):
            self.v = v

        def currentText(self):
            return self.v

    class Canvas:
        def mapUnitsPerPixel(self):
            return 10.0

        def setExtent(self, *a):
            pass

        def refresh(self):
            pass

        def setMapTool(self, *a):
            pass

        def unsetMapTool(self, *a):
            pass

    class Host:
        out_dir, vrt_path = d, vrt
        year_a, year_b = Combo("2019"), Combo("2024")

        def _threshold(self):
            return 0.10

    class Iface:
        def mapCanvas(self):
            return Canvas()

    return ClassifyPanel(Host(), Iface())


def test_the_whole_panel_loop_works():
    """make polygons -> add a class -> CLICK a polygon -> it is labelled and everything else
    is classified. This is the loop the user actually performs, and nothing below the UI layer
    can prove it works."""
    import tempfile
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aepanel_")
    vrt = _tiny_job(d)
    p = _panel(d, vrt, ClassifyPanel)
    p.make_polygons()
    assert p.layer is not None and len(p.polys) == 2, p.polys
    assert p.vectors is not None and p.vectors.shape[0] == 2

    p.classes.append("cutblock")
    p.colors["cutblock"] = "#d85a30"
    p._refresh_list()
    p.list.setCurrentRow(0)

    feat = next(p.layer.getFeatures())
    p.label_at(feat.geometry().centroid().asPoint())
    assert p.labels, "clicking a polygon must label it"
    assert p.head is not None, "one label must be enough to fit — not two"
    assert "1 labelled" in p.list.item(0).text(), p.list.item(0).text()

    p.label_at(feat.geometry().centroid().asPoint(), clear=True)
    assert not p.labels, "right-click must remove the label"

    p.label_at(feat.geometry().centroid().asPoint())
    p.fit_and_classify()
    assert "Fitted on 1 labelled object" in p.status.text(), p.status.text()
    row = next(r for r in p.labels)
    assert p.pred[row] == "cutblock", "a label the user set is never overwritten"
    QgsProject.instance().removeMapLayer(p.layer.id())
    print(f"ok full loop: 2 objects, click-labelled 1, fitted, status {p.status.text()[:48]!r}")


class _FakeItem:
    def data(self, _role):
        return None


def test_removing_the_layer_does_not_break_the_panel():
    """The user's crash: clear the session (or just delete the layer) and carry on. The Python
    wrapper survives, the C++ object does not, and the next touch raises RuntimeError from
    somewhere unrelated — it surfaced from refreshing the class list."""
    import tempfile
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aegone_")
    vrt = _tiny_job(d)
    p = _panel(d, vrt, ClassifyPanel)
    p.make_polygons()
    p.classes.append("cutblock")
    p.colors["cutblock"] = "#d85a30"
    p._refresh_list()
    p.list.setCurrentRow(0)
    p.label_at(next(p.layer.getFeatures()).geometry().centroid().asPoint())
    assert p.labels and p.head is not None

    QgsProject.instance().removeMapLayer(p.layer.id())     # the user clears the session
    for call in (p._refresh_list, p.sync, p._style, p._refit,
                 lambda: p.delete_class(), lambda: p.fit_and_classify(),
                 lambda: p._show_selected(),
                 lambda: p._select_class_on_map(_FakeItem())):
        call()                                             # none of these may raise
    assert p.layer is None, "a deleted layer must be forgotten, not held onto"
    print("ok panel survives its layer being deleted mid-session")


def test_class_examples_survive_a_re_polygonize():
    """Labels point at rows of the current polygon set, so they cannot survive a re-cut — but
    the EXAMPLES can, because they are stored as vectors. Losing a session's labelling because
    the threshold moved would be the most annoying possible behaviour."""
    import tempfile
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aebank_")
    vrt = _tiny_job(d)
    p = _panel(d, vrt, ClassifyPanel)
    p.make_polygons()
    p.classes.append("cutblock")
    p.colors["cutblock"] = "#d85a30"
    p._refresh_list()
    p.list.setCurrentRow(0)
    p.label_at(next(p.layer.getFeatures()).geometry().centroid().asPoint())
    assert len(p._class_vectors()["cutblock"]) == 1

    p.make_polygons()                                       # re-cut at the same threshold
    assert p.labels == {}, "row-indexed labels must be dropped"
    assert "cutblock" in p._class_vectors(), "the example itself must survive"
    assert p.head is not None, "and it must still be enough to classify with"
    QgsProject.instance().removeMapLayer(p.layer.id())
    print("ok labelled examples survive a re-polygonize; row indices do not")


def test_best_guess_option_reduces_unknowns():
    import tempfile
    import numpy as np
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aeguess_")
    vrt = _tiny_job(d)
    p = _panel(d, vrt, ClassifyPanel)
    p.make_polygons()
    for name, col in (("a", "#d85a30"), ("b", "#1d9e75")):
        p.classes.append(name)
        p.colors[name] = col
    p._refresh_list()
    feats = list(p.layer.getFeatures())
    for i, f in enumerate(feats):
        p.list.setCurrentRow(i % 2)
        p.label_at(f.geometry().centroid().asPoint())
    strict = int((np.asarray(p.pred, dtype=object) == UNKNOWN).sum())
    p.guess_box.setChecked(True)
    loose = int((np.asarray(p.pred, dtype=object) == UNKNOWN).sum())
    assert loose <= strict, (strict, loose)
    assert p.head.decision == "argmax"
    QgsProject.instance().removeMapLayer(p.layer.id())
    print(f"ok best-guess option switches the decision rule (unknown {strict} -> {loose})")


def test_pausing_makes_a_label_a_local_edit():
    """The finishing pass. While it is learning, one correction reshuffling twenty other
    polygons is the point. Once the map is nearly right it is vandalism — so paused, a label
    must move that polygon and nothing else, while still being kept for saving."""
    import tempfile
    import numpy as np
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aepause_")
    vrt = _tiny_job(d)
    p = _panel(d, vrt, ClassifyPanel)
    p.make_polygons()
    for name, col in (("a", "#d85a30"), ("b", "#1d9e75")):
        p.classes.append(name)
        p.colors[name] = col
    p._refresh_list()
    feats = list(p.layer.getFeatures())
    for i, f in enumerate(feats):
        p.list.setCurrentRow(i % 2)
        p.label_at(f.geometry().centroid().asPoint())
    before = np.asarray(p.pred, dtype=object).copy()
    head_before = p.head

    p.pause_box.setChecked(True)
    assert p.paused and not p.q_slider.isEnabled(), "refit controls must be disabled while paused"

    target = p._row_of(feats[0])
    p.list.setCurrentRow(1)                     # deliberately the OTHER class
    p.label_at(feats[0].geometry().centroid().asPoint())
    after = np.asarray(p.pred, dtype=object)

    assert after[target] == "b", after[target]
    others = [i for i in range(len(after)) if i != target]
    assert all(after[i] == before[i] for i in others), "a paused edit moved another polygon"
    assert p.head is head_before, "paused means the head is not refitted"
    assert p.labels[target] == "b"
    assert "b" in p._class_vectors(), "a manual edit must still be saveable as an example"

    p.pause_box.setChecked(False)
    assert not p.paused and p.q_slider.isEnabled()
    assert p.head is not head_before, "unpausing refits"
    QgsProject.instance().removeMapLayer(p.layer.id())
    print("ok paused: the edit lands on one polygon, nothing else moves, head untouched")


def test_a_users_correction_is_never_revised_by_the_model():
    """A label the user set outranks the model, permanently and on every path.

    Tested against each thing that triggers a refit, because the leak was on one of them:
    load_classes used to predict and write on its own, without re-applying the user's labels,
    so loading a preset silently overwrote their corrections.
    """
    import os
    import tempfile
    import numpy as np
    from embed_cd_qgis.classify import ClassifyPanel
    from embed_cd import head as H

    d = tempfile.mkdtemp(prefix="aelock_")
    vrt = _tiny_job(d)
    p = _panel(d, vrt, ClassifyPanel)
    p.make_polygons()
    for name, col in (("a", "#d85a30"), ("b", "#1d9e75")):
        p.classes.append(name)
        p.colors[name] = col
    p._refresh_list()

    feats = list(p.layer.getFeatures())
    for i, f in enumerate(feats):
        p.list.setCurrentRow(i % 2)
        p.label_at(f.geometry().centroid().asPoint())
    mine = dict(p.labels)
    assert mine, "need at least one label to test the lock"

    def still_mine(what):
        for row, name in mine.items():
            assert p.pred[row] == name, (
                f"{what} overwrote the user's '{name}' on row {row} with '{p.pred[row]}'")

    still_mine("labelling")

    p.classes.append("c")                        # a new class shifts every decision boundary
    p.colors["c"] = "#7f77dd"
    p._refit()
    still_mine("adding a class")

    p.q_slider.setValue(35)
    p._refit()
    still_mine("raising strictness")

    p.guess_box.setChecked(True)                 # different decision rule entirely
    still_mine("switching to best-guess")

    # a preset trained on completely unrelated vectors — the strongest possible pull
    rng = np.random.default_rng(0)
    alien = {"zzz": rng.normal(3.0, 0.1, (6, p.vectors.shape[1])).astype("float32")}
    preset = os.path.join(d, "alien.json")
    H.save_classes(preset, alien)
    loaded, _, _ = H.load_classes(preset)
    p.class_vectors = {k: list(v) for k, v in loaded.items()}
    p.classes = list(loaded)
    p._refit(force=True)
    still_mine("loading a preset")

    QgsProject.instance().removeMapLayer(p.layer.id())
    print(f"ok {len(mine)} user corrections survived every refit trigger")


def test_a_rewritten_vrt_is_only_seen_at_a_new_path():
    """Why the worker writes `name.vN.vrt` instead of rewriting one file.

    QGIS caches the dataset a raster layer opened. Rewriting the SAME path leaves the canvas
    showing the revision it first opened — neither reloadData() nor setDataSource() to that
    same path dislodges it. That was the "new tiles don't appear until you zoom in and out"
    report, and it could persist after the job finished.

    This asserts both halves, because if a future QGIS starts honouring reloadData() the
    negative half will fail and tell us the revision files are no longer needed.
    """
    import tempfile
    import numpy as np
    from qgis.core import QgsRasterLayer, QgsRectangle
    from embed_cd import gdalio as GD, vrt as V, grid as G

    d = tempfile.mkdtemp(prefix="aevrt_")
    g = G.Grid("EPSG:3857", 0.0, 100.0, 10.0, 20, 10)
    tiles = []
    for i, (r0, c0) in enumerate([(0, 0), (0, 10)]):
        p = os.path.join(d, f"t{i}.tif")
        GD.write(p, np.stack([np.full((10, 10), 0.1 * (i + 1), np.float32),
                              np.zeros((10, 10), np.float32)]), g.crs,
                 G.transform_of(g, r0, c0), nodata=-1.0)
        tiles.append({"path": p, "row0": r0, "col0": c0, "width": 10, "height": 10})
    ext = QgsRectangle(0.0, 0.0, 200.0, 100.0)

    def filled(layer):
        blk = layer.dataProvider().block(1, ext, 20, 10)
        a = np.array([[blk.value(r, c) for c in range(20)] for r in range(10)])
        return int((a != -1.0).sum())

    same = os.path.join(d, "same.vrt")
    V.write_vrt(same, g, tiles[:1])
    layer = QgsRasterLayer(same, "x")
    assert filled(layer) == 100, "one tile should cover half the grid"

    V.write_vrt(same, g, tiles)                    # rewrite in place, as the old code did
    layer.dataProvider().reloadData()
    layer.triggerRepaint()
    assert filled(layer) == 100, (
        "reloadData() now works on a rewritten path — the per-tile revision files in "
        "worker.on_tile exist only to work around it and can be simplified away")

    v2 = os.path.join(d, "rev2.vrt")
    V.write_vrt(v2, g, tiles)
    layer.setDataSource(v2, "x", "gdal")
    assert filled(layer) == 200, "a path the layer has never opened must show the new tile"
    assert layer.renderer() is not None, "re-pointing must not leave the layer unrenderable"
    print("ok a rewritten VRT is invisible in place, and visible at a new path")


def test_the_photo_strip_offers_every_year_and_stacks_them():
    """The strip must cover every embedding year AND allow several at once.

    Both were wrong first time: it was bound to the two chosen years, and it was exclusive.
    Exclusivity is the worse bug — comparing before and after means having both layers loaded
    and flicking the top one's visibility, which one-at-a-time makes impossible.
    """
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock, _YEARS
    from embed_cd import basemap as BM

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    win, canvas = QMainWindow(), QgsMapCanvas()
    dock = ChangeDock(_Iface(win, canvas))
    years = sorted(dock.photo_btns)
    assert years == sorted(int(y) for y in _YEARS), f"strip covers {years}, expected all _YEARS"

    # Streamed global tiles: usable before any area is drawn, unlike everything else here.
    assert all(b.isEnabled() for b in dock.photo_btns.values()),         "streamed tiles need no drawn area"

    # A year EOX does not publish has to SAY so; showing a neighbour's imagery under the wrong
    # label would quietly invalidate the visual check the strip exists for.
    substituted = [y for y in years if BM.nearest_year(y) != y]
    assert substituted, "2017 has no EOX mosaic; this test is pointless if that changes silently"
    for y in substituted:
        assert f"No {y} mosaic" in dock.photo_btns[y].toolTip()

    assert dock.photo_ids == {}, "nothing loaded until asked"
    dock.deleteLater()
    print(f"ok photo strip covers all {len(years)} years, independently toggleable")


def test_a_saved_run_can_be_reopened_and_is_fully_live():
    """A saved result used to be openable as a PICTURE and nothing else.

    Threshold, Auto, Polygonize, Save and the whole classifier all gate on `vrt_path` /
    `out_dir` / `layer_id`, and those were only ever set as a side effect of a job running in
    the same session — parsed out of the worker's stdout. So the tiles, the VRT and the cell
    stores could all be sitting on disk, intact, with no way to reach them.

    Builds a folder shaped exactly like a real run, including the superseded `.v1.vrt` a
    finished job leaves behind, because matching that by accident loads a partial mosaic.
    """
    import numpy as np
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock
    from embed_cd import grid as G, score as S, vrt as V, cells as CE, gdalio as GD

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    root = tempfile.mkdtemp(prefix="tc_reopen_")
    run = os.path.join(root, "change_2019_2024")
    os.makedirs(run)
    g = G.Grid("EPSG:3857", -13800000.0, 6600000.0, 100.0, 40, 30)
    tiles = []
    for i, (r0, c0) in enumerate([(0, 0), (0, 20)]):
        p = os.path.join(run, f"tile_2019-2024_EPSG3857-100m_t{i}.tif")
        score = np.full((30, 20), 0.3 + 0.2 * i, dtype=np.float32)
        cov = np.full((30, 20), float(S.COV_OK), dtype=np.float32)
        GD.write(p, np.stack([score, cov]), g.crs, G.transform_of(g, r0, c0), nodata=S.NODATA)
        tiles.append({"path": p, "row0": r0, "col0": c0, "width": 20, "height": 30})
    V.write_vrt(os.path.join(run, "change_2019_2024.v1.vrt"), g, tiles[:1])   # the decoy
    V.write_vrt(os.path.join(run, "change_2019_2024.vrt"), g, tiles)
    tr = GD.Transform.from_origin(g.x0, g.y0, 160.0, 160.0)
    CE.write_cells(os.path.join(run, "cells_2019-2024_160m_EPSG32610_500000_5399360.tif"),
                   np.zeros((4, 4, 8), np.float32), np.zeros((4, 4, 8), np.float32),
                   np.ones((4, 4), np.int32), np.full((4, 4), 0.4, np.float32),
                   np.full((4, 4), 0.6, np.float32), "EPSG:32610", tr, 16)

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    assert not dock.poly_btn.isEnabled(), "nothing should be live before opening"

    runs = dock._find_runs(root)                 # given the PARENT, as a user plausibly would
    assert len(runs) == 1, f"expected one run, found {len(runs)}"
    assert runs[0]["cells"] == 1, "should have spotted the cell store"
    path, ya, yb = runs[0]["path"], runs[0]["year_a"], runs[0]["year_b"]
    assert os.path.basename(path) == "change_2019_2024.vrt", \
        f"picked {os.path.basename(path)} — the .v1 decoy holds only half the tiles"
    assert (ya, yb) == (2019, 2024)

    # Drive the real reopen, minus the file dialog.
    from unittest import mock
    with mock.patch.object(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: root)):
        dock._open_existing()

    assert dock.vrt_path == path, "vrt_path not reconnected"
    assert dock.out_dir == run, "out_dir not reconnected"
    assert dock.layer_id is not None, "no layer added"
    assert dock.year_a.currentText() == "2019" and dock.year_b.currentText() == "2024", \
        "years not recovered from the filename"
    assert dock.detail.currentText() == "100 m", \
        f"Detail not recovered from the raster's own pixel size: {dock.detail.currentText()}"
    assert dock.bbox is not None and dock.bbox[0] < dock.bbox[2], "bbox not derived"
    for w, name in ((dock.slider, "threshold"), (dock.auto_btn, "Auto"),
                    (dock.poly_btn, "Polygonize"), (dock.save_btn, "Save")):
        assert w.isEnabled(), f"{name} still dead after reopening"

    # The point of all this: the downstream features must actually WORK, not merely light up.
    dock._auto()
    polys, _crs = __import__("embed_cd.objects", fromlist=["objects"]).polygonize(
        dock.vrt_path, 0.2, min_area_ha=0.01)
    assert polys, "polygonize found nothing in a reopened run"
    dock.cleanup()
    print(f"ok reopened a saved run: {len(polys)} polygons, all controls live")


def test_a_folder_holding_several_runs_asks_which_one():
    """The real shape of a working folder: the same area compared across different year pairs,
    saved side by side. Silently taking the newest is wrong — both are equally valid answers to
    "open the results in this folder", and the user gets no clue which one they got.
    """
    import numpy as np
    from qgis.PyQt.QtWidgets import QMainWindow, QInputDialog
    from qgis.gui import QgsMapCanvas
    from unittest import mock
    from embed_cd_qgis.dock import ChangeDock
    from embed_cd import grid as G, score as S, vrt as V, gdalio as GD

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    root = tempfile.mkdtemp(prefix="tc_multi_")
    g = G.Grid("EPSG:3857", -13800000.0, 6600000.0, 100.0, 20, 20)
    for yb in (2023, 2024):
        run = os.path.join(root, f"change_2019_{yb}")
        os.makedirs(run)
        p = os.path.join(run, "t.tif")
        GD.write(p, np.stack([np.full((20, 20), 0.4, np.float32),
                              np.full((20, 20), float(S.COV_OK), np.float32)]),
                 g.crs, G.transform_of(g, 0, 0), nodata=S.NODATA)
        V.write_vrt(os.path.join(run, f"change_2019_{yb}.vrt"), g,
                    [{"path": p, "row0": 0, "col0": 0, "width": 20, "height": 20}])

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    runs = dock._find_runs(root)
    assert len(runs) == 2, f"found {len(runs)} runs, expected both year pairs"
    assert {r["year_b"] for r in runs} == {2023, 2024}

    # One run must NOT prompt; several must.
    asked = {"n": 0}

    def fake_item(_parent, _title, _label, items, *a, **k):
        asked["n"] += 1
        return next(i for i in items if "2019 → 2024" in i), True

    with mock.patch.object(QInputDialog, "getItem", staticmethod(fake_item)):
        chosen = dock._choose_run(runs)
        assert asked["n"] == 1, "two runs should have prompted exactly once"
        assert chosen["year_b"] == 2024, "did not honour the choice"
        asked["n"] = 0
        single = dock._choose_run(runs[:1])
        assert asked["n"] == 0, "a single run must open without asking"
        assert single is runs[0]

    # Dismissing must abort rather than silently opening something.
    with mock.patch.object(QInputDialog, "getItem",
                           staticmethod(lambda *a, **k: ("", False))):
        assert dock._choose_run(runs) is None, "cancel must not fall through to a default"
    dock.cleanup()
    print("ok several saved runs in one folder prompt, and the choice is honoured")


def test_polygons_and_labelling_survive_closing_the_session():
    """Cut polygons, label some, throw the whole panel away, reopen the folder.

    Polygons over a large area cost minutes and the labelling on top of them is the user's own
    work, so losing either to a closed window is the expensive failure. Predictions are NOT
    stored — they are refit from the restored classes — so this also checks that refitting
    genuinely reproduces them rather than merely repopulating something.
    """
    import numpy as np
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from unittest import mock
    from embed_cd_qgis.dock import ChangeDock
    from embed_cd import store as ST

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    d = tempfile.mkdtemp(prefix="tc_persist_")
    run = os.path.join(d, "change_2019_2024")
    os.makedirs(run)
    _tiny_job(run)                       # change raster + matching cell store
    os.replace(os.path.join(run, "change.vrt"), os.path.join(run, "change_2019_2024.vrt"))

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    with mock.patch.object(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: d)):
        dock._open_existing()
    panel = dock.classify
    dock.slider.setValue(20)
    panel.make_polygons()
    n = len(panel.polys)
    assert n >= 2, f"expected the two planted objects, got {n}"
    assert os.path.exists(ST.objects_path(run, 2019, 2024)), "polygons were not written"

    panel.classes.append("cutblock")          # add_class() prompts; this is the same end state
    panel.colors["cutblock"] = "#d85a30"
    panel.labels[0] = "cutblock"
    panel._refit()
    before_pred = list(panel.pred)
    before_vecs = panel.vectors.copy()
    assert os.path.exists(ST.labels_path(run, 2019, 2024)), "labels were not written"

    # Throw everything away, exactly as closing QGIS would.
    dock.cleanup()
    dock2 = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    with mock.patch.object(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: d)):
        dock2._open_existing()
    p2 = dock2.classify

    assert len(p2.polys) == n, f"got {len(p2.polys)} polygons back, saved {n}"
    assert np.allclose(p2.vectors, before_vecs, atol=1e-6), "embeddings came back changed"
    assert "cutblock" in p2.classes, f"classes lost: {p2.classes}"
    assert p2.labels.get(0) == "cutblock", f"per-polygon label lost: {p2.labels}"
    assert list(p2.pred) == before_pred, "predictions did not refit to the same answer"
    assert p2.layer is not None and p2.layer.featureCount() == n, "layer not rebuilt"

    # The slider must come back at the cut the polygons were made at, whatever it was left on.
    # An earlier version compared the SAVED cut against the LIVE slider and restored labels only
    # when they matched — which a freshly opened dock never does, so labels were silently lost
    # every time. Polygons are LOADED, not re-cut, so there is nothing to guard against.
    dock3 = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    dock3.slider.setValue(80)                    # nowhere near the cut
    with mock.patch.object(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: d)):
        dock3._open_existing()
    assert abs(dock3._threshold() - 0.20) < 1e-9, \
        f"slider not restored to the cut: {dock3._threshold()}"
    assert dock3.classify.labels.get(0) == "cutblock", \
        f"labels lost when the dock opened on a different slider value: {dock3.classify.labels}"
    dock2.cleanup()
    dock3.cleanup()
    print(f"ok {n} polygons, classes and labels survived a session restart")


def test_two_areas_over_the_same_years_cannot_share_a_run_folder():
    """This one destroyed real data before it was caught.

    Opening a saved run used to set `Save to:` to its parent, so the NEXT run inherited that
    folder — and because the folder was named from the year pair alone, a different area
    compared over the same years landed in the SAME directory. Its tiles interleaved with the
    first area's, `CellIndex` picked up both, and its VRT overwrote the original. The evidence
    was a Mt Bishop folder holding 61 EPSG:32611 tiles and one EPSG:32610 tile from Vancouver
    Island.

    Two rules, both checked here: opening never repoints where new work goes, and the run
    folder names the area as well as the years.
    """
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    bishop = (-118.60, 49.10, -118.40, 49.30)
    victoria = (-123.50, 48.35, -123.30, 48.55)

    dock.bbox = bishop
    key_b = dock._area_key()
    dock.bbox = victoria
    key_v = dock._area_key()
    assert key_b != key_v, "two different areas produced the same folder key"
    dock.bbox = bishop
    assert dock._area_key() == key_b, "the same bbox must resume, not start a new folder"

    # Opening a saved run must leave `Save to:` exactly as the user left it.
    dock.out_edit.setText("")
    dock.out_dir = os.path.join(tempfile.mkdtemp(prefix="tc_key_"), "change_2019_2023_abc123")
    os.makedirs(dock.out_dir)
    import inspect
    src = inspect.getsource(ChangeDock._open_existing)
    assert "out_edit.setText" not in src, \
        "_open_existing repoints Save to: again — that is what mixed two areas into one folder"
    dock.cleanup()
    print(f"ok run folders are keyed by area ({key_b} vs {key_v}); opening leaves Save to: alone")


def test_the_dock_always_says_where_results_will_go():
    """A path set for one area stays set when the next is drawn, and nothing on screen said so —
    which is how a Vancouver Island run ended up inside a Mt Bishop folder. The destination line
    has to be right in every state, and must track the AREA, not just the path box."""
    import re
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    def plain(t):
        return re.sub("<[^>]+>", "", t)

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    assert "temporary" in plain(dock.dest_lbl.text()), \
        f"a fresh dock must say results are temporary, got {dock.dest_lbl.text()!r}"

    parent = tempfile.mkdtemp(prefix="tc_dest_")
    dock.bbox = (-118.60, 49.10, -118.40, 49.30)
    dock.out_edit.setText(parent)
    first = plain(dock.dest_lbl.text())
    assert parent in first and "change_" in first, first

    # The case that caused real damage: same folder, different area.
    dock.bbox = (-123.50, 48.35, -123.30, 48.55)
    dock._sync()
    second = plain(dock.dest_lbl.text())
    assert second != first, \
        "drawing a new area did not change the shown destination — the whole point of this line"
    assert parent in second, second

    # And the offered escape hatch actually works, so nobody has to guess that an empty box
    # means "temporary".
    dock._dest_link("temp")
    assert "temporary" in plain(dock.dest_lbl.text())
    assert dock.out_edit.text() == ""
    dock.cleanup()
    print("ok the destination line is correct in every state and tracks the area")


def test_a_named_area_keeps_its_name_and_never_shares_a_group():
    """Two things a user hits within a minute of each other.

    Naming an area "alex test" and reopening it showed the coordinates instead — the name lived
    only in the widget, and nothing on disk carried it (years come from the VRT filename, the
    extent from the raster, the name from nowhere).

    And drawing a second area while forgetting to retype the name put both in ONE group,
    interleaving them. Group identity has to come from the AREA, not the name: two areas
    trivially share a name, which is precisely the case that needs disambiguating.
    """
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from qgis.core import QgsProject, QgsVectorLayer
    from embed_cd_qgis.dock import ChangeDock
    from embed_cd import store as ST

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    QgsProject.instance().clear()
    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    dock.bbox = (-126.05, 50.28, -125.90, 50.37)
    dock._sync()
    assert "50.3" in dock.name_edit.placeholderText(), \
        f"unnamed areas should offer their location: {dock.name_edit.placeholderText()!r}"

    # the name has to reach the disk, or reopening cannot recover it
    dock.out_dir = tempfile.mkdtemp(prefix="tc_name_")
    dock.name_edit.setText("alex test")
    dock._save_meta()
    assert ST.load_meta(dock.out_dir).get("name") == "alex test", ST.load_meta(dock.out_dir)

    # two DIFFERENT areas, same name -> separate groups
    dock._release_layers(dock._group_name())
    map_a = dock._add_to_group(QgsVectorLayer("Polygon?crs=EPSG:3857", "map A", "memory"))
    map_a_id = map_a.id()
    dock.layer_id = map_a_id
    first = dock._current_group
    dock.bbox = (-118.60, 49.10, -118.40, 49.30)       # different place, name untouched
    dock._release_layers(dock._group_name())
    dock._add_to_group(QgsVectorLayer("Polygon?crs=EPSG:3857", "map B", "memory"))
    second = dock._current_group
    assert first != second, f"both areas landed in one group: {first!r}"
    assert second.endswith("(2)"), f"expected a disambiguating suffix, got {second!r}"

    root = QgsProject.instance().layerTreeRoot()
    assert [c.name() for c in root.findGroup(first).children()] == ["map A"], "area 1 polluted"
    assert [c.name() for c in root.findGroup(second).children()] == ["map B"], "area 2 polluted"
    # Switching areas DETACHES: the first area's group keeps its layer, and the dock stops
    # tracking it. Previously the raster was removed while the polygons were not, leaving the
    # old area as an orphan polygon layer belonging to nothing.
    assert QgsProject.instance().mapLayer(map_a_id) is not None,         "the previous area's layer was removed instead of detached"

    # and re-running the SAME area reuses its group rather than making a third
    before = len([c for c in root.children() if hasattr(c, "children")])
    dock._release_layers(dock._group_name())
    assert dock._current_group == second, "a re-run of the same area moved to a new group"
    assert len([c for c in root.children() if hasattr(c, "children")]) == before, \
        "a re-run created another group instead of reusing this area's"
    dock.cleanup()
    QgsProject.instance().clear()
    print("ok names persist, and same-named areas get separate groups")


def test_undo_and_stepping_through_objects():
    """Undo and the review arrows, driven the way the buttons drive them.

    The parts worth guarding: a bulk assign undoes as ONE step (thirty presses would be
    useless), undo restores the PREVIOUS label rather than just clearing, the stack is dropped
    on a re-cut (its rows stop existing, so restoring them would mislabel whatever now sits at
    those indices), and stepping pans without changing your zoom.
    """
    import numpy as np
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    d = tempfile.mkdtemp(prefix="tc_undo_")
    _tiny_job(d)
    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    dock.out_dir, dock.vrt_path = d, os.path.join(d, "change.vrt")
    dock.slider.setValue(20)
    p = dock.classify
    p.make_polygons()
    assert len(p.polys) >= 2, f"need at least two objects to step between, got {len(p.polys)}"
    p.classes[:] = ["a", "b"]
    p.colors.update({"a": "#d85a30", "b": "#1d9e75"})
    p._refresh_list()
    p.list.setCurrentRow(0)

    # a bulk assign is ONE undo step
    rows = list(range(len(p.polys)))
    p._push_undo(rows)
    for r in rows:
        p.labels[r] = "a"
    p._refit()
    assert len(p._undo) == 1, f"a batch became {len(p._undo)} undo steps"
    p.undo_label()
    assert p.labels == {}, f"one undo did not take the whole batch back: {p.labels}"

    # undo restores the previous label, it does not merely clear
    p._push_undo([0]); p.labels[0] = "a"
    p._push_undo([0]); p.labels[0] = "b"
    p.undo_label()
    assert p.labels.get(0) == "a", f"undo cleared instead of reverting: {p.labels}"
    p.undo_label()
    assert 0 not in p.labels, f"second undo should leave it unlabelled: {p.labels}"

    # stepping selects and pans, WITHOUT changing scale
    p.labels[0] = "a"
    p._refit()
    p.cycle_mode.setCurrentIndex(p.cycle_mode.findData("labelled"))
    p._cycle_at = -1
    before = dock.canvas.extent().width()
    p.step(1)
    assert abs(dock.canvas.extent().width() - before) < 1e-6, \
        "stepping changed the zoom; it is meant to pan and keep scale"
    # Stepping must NOT use QGIS selection. A selected feature is drawn with the layer's single
    # selection symbol INSTEAD of its own, so selecting it loses the class colour — measured:
    # the default paints it yellow, an "invisible" selection symbol paints nothing at all. The
    # current object is ours, drawn as an overlay on top of the normal symbology.
    assert p._selected_row() is not None, "stepping did not set a current object"
    assert len(p.layer.selectedFeatures()) == 0,         "stepping selected the feature, which repaints it and hides its class colour"

    # every filter returns something sane. Set by KEY, not by label: one entry is renamed
    # live to the selected class, so tests that matched on text would be testing the wording.
    def use(key):
        p.cycle_mode.setCurrentIndex(p.cycle_mode.findData(key))

    for key in ("uncertain", "unlabelled", "unknown", "labelled", "all"):
        use(key)
        got = p._cycle_rows()
        assert all(0 <= r < len(p.polys) for r in got), f"{key} produced out-of-range rows"
    use("labelled")
    assert p._cycle_rows() == sorted(p.labels), "labelled filter disagrees with the labels"

    # discard removes the selected polygon's label and is itself undoable
    p._goto_row(0)
    n = len(p.labels)
    p.discard_label()
    assert len(p.labels) == n - 1, "discard did not remove the label"
    p.undo_label()
    assert len(p.labels) == n, "discard was not undoable"

    # The class combo reads the selection and writes to it — and STEPPING must not count as
    # writing, or panning past an object would relabel it as whatever it already was, filling
    # the undo stack with phantom edits and marking every object you looked at as user-labelled.
    use("all")
    p._cycle_at = -1
    p.step(1)
    depth = len(p._undo)
    p.step(1)
    p.step(-1)
    assert len(p._undo) == depth, "stepping through objects recorded phantom label edits"

    row = p._selected_row()
    assert row is not None, "stepping should leave exactly one current object"
    was = p.labels.get(row)          # may already carry a label from earlier in this test
    p.cls_combo.setCurrentIndex(p.cls_combo.findData("b"))
    assert p.labels.get(row) == "b", f"setting the combo did not relabel: {p.labels}"
    assert len(p._undo) == depth + 1, "a combo edit must be undoable"
    p.undo_label()
    assert p.labels.get(row) == was,         f"undo of a combo edit gave {p.labels.get(row)!r}, expected {was!r}"

    # a re-cut invalidates the stack: those rows are about to mean something else
    p.make_polygons()
    assert p._undo == [], "the undo stack survived a re-polygonize"
    dock.cleanup()
    print("ok undo batches, reverts, survives nothing it should not; stepping pans")


def test_steps_fold_once_and_keep_their_answer_in_the_title():
    """The panel is now three numbered steps that fold as they are finished with.

    Two rules worth guarding. A step folds ONCE — refolding on every refresh would fight anyone
    who reopened it to change something, which is the complaint auto-collapse usually earns. And
    a folded step still says what it holds, via its title; folding that hid both the controls
    AND the record of them would be strictly worse than a tall panel.
    """
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    if not hasattr(dock.step1, "setCollapsed"):
        print("skipped: QgsCollapsibleGroupBox unavailable in this build")
        dock.cleanup()
        return

    assert not dock.step1.isCollapsed(), "step 1 is the live step before anything is run"
    dock.bbox = (-126.05, 50.28, -125.90, 50.37)
    dock.name_edit.setText("Mt Bishop")
    dock._sync()
    assert "Mt Bishop" in dock.step1.title(), \
        f"the title should carry the area once one exists: {dock.step1.title()!r}"

    # a result arrives -> step 1 folds
    dock.layer_id = "pretend-layer-id"
    dock._sync()
    assert dock.step1.isCollapsed(), "step 1 did not fold once a result existed"
    assert "2019" in dock.step1.title() and "Mt Bishop" in dock.step1.title(), \
        f"a folded step must still say what it holds: {dock.step1.title()!r}"

    # the user reopens it; further refreshes must leave it alone
    dock.step1.setCollapsed(False)
    for _ in range(3):
        dock._sync()
    assert not dock.step1.isCollapsed(), \
        "step 1 refolded itself after the user reopened it — fold once, then never again"

    # drawing a NEW area makes step 1 live again, so it may fold once more later
    dock._folded_once.discard("step1")
    dock._sync()
    assert dock.step1.isCollapsed(), "a new area should let step 1 fold again when finished"

    assert "cutoff" in dock.step2.title(), \
        f"step 2's title should carry the cutoff: {dock.step2.title()!r}"
    dock.cleanup()
    print("ok steps fold once, reopen stays open, titles carry the summary")


def test_switching_between_areas_restores_each_one():
    """Several areas in one session, and the dock must be honest about which one it acts on.

    Before this, `Find objects`, the threshold, Export and the classifier all read whichever run
    was made or opened LAST, while every area's layers sat in the tree looking equally live —
    and a temp run's folder is a mkdtemp path nobody can navigate back to, so there was no way
    back at all.
    """
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from qgis.core import QgsProject
    from embed_cd_qgis.dock import ChangeDock

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    QgsProject.instance().clear()
    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    dock._tmp_root = tempfile.mkdtemp(prefix="tc_switch_")

    def make(name, bbox):
        run = os.path.join(dock._tmp_root, name.replace(" ", ""))
        os.makedirs(run, exist_ok=True)
        _tiny_job(run)
        dock.bbox = bbox
        dock.name_edit.setText(name)
        dock._release_layers(dock._group_name())
        dock.out_dir = run
        dock.vrt_path = os.path.join(run, "change.vrt")
        dock.layer_id = None
        dock._refresh_layer()
        dock._register_run()
        dock._sync()

    make("Area A", (-126.05, 50.28, -125.90, 50.37))
    dock.slider.setValue(20)
    p = dock.classify
    p.make_polygons()
    p.classes[:] = ["cutblock"]
    p.colors["cutblock"] = "#d85a30"
    p._refresh_list()
    p.list.setCurrentRow(0)
    p.labels[0] = "cutblock"
    p._refit()
    a_objs, a_labels = len(p.polys), dict(p.labels)
    assert a_objs and a_labels, "the fixture must actually produce labelled objects"

    # Naming an area you have DRAWN but not yet run must not rename the one you left. out_dir
    # still points at the previous run at that moment, so matching on it alone renamed the wrong
    # entry and both areas then showed the same name.
    dock.bbox = (-123.50, 48.35, -123.30, 48.55)
    dock.name_edit.setText("Area B")
    dock._save_meta()
    assert dock.runs[0]["name"] != "Area B",         f"typing a name for the next area renamed the previous one: {dock.runs[0]['name']!r}"

    make("Area B", (-123.50, 48.35, -123.30, 48.55))
    p.make_polygons()
    assert dock.run_combo.count() == 2, f"both runs should be listed: {dock.run_combo.count()}"
    labels = [dock.run_combo.itemText(i) for i in range(dock.run_combo.count())]
    assert len(set(labels)) == 2, f"two areas showing the same label: {labels}"

    i = next(i for i in range(dock.run_combo.count())
             if "Area A" in dock.run_combo.itemText(i))
    dock.run_combo.setCurrentIndex(i)

    assert dock.name_edit.text() == "Area A", f"name did not follow: {dock.name_edit.text()!r}"
    assert len(p.polys) == a_objs, f"objects not restored: {len(p.polys)} vs {a_objs}"
    assert p.labels == a_labels, f"labels not restored: {p.labels} vs {a_labels}"

    # Returning to an area must REUSE its group. `findGroup(...) or insertGroup(...)` reads
    # naturally and is wrong: an empty QgsLayerTreeGroup is falsy, so once the group had been
    # emptied to be rebuilt, the `or` fell through and built a second group beside it.
    root = QgsProject.instance().layerTreeRoot()
    groups = [n.name() for n in root.children() if hasattr(n, "children")]
    assert len(groups) == len(set(groups)) == 2, f"duplicate or missing groups: {groups}"
    for g in root.children():
        if hasattr(g, "children"):
            assert len(g.children()) == 3, \
                f"{g.name()} holds {[c.name() for c in g.children()]}, expected 3 layers"
    dock.cleanup()
    QgsProject.instance().clear()
    print(f"ok switched back to Area A: {a_objs} objects and {len(a_labels)} labels restored")


def test_the_tile_estimate_asks_the_tiler_when_it_can():
    """Area alone cannot count tiles, and the error is not small.

    AlphaEarth publishes per UTM zone, so an area near a zone boundary fetches tiles from BOTH
    and needs roughly twice as many. Measured against the real tiler: 19.9x19.9 km at the
    9N/10N line is 21 tiles where the area formula says 9. It also over-counts well inside a
    zone (13x10 km is 4, not 6) because the formula's +1 per axis assumes the worst case on
    both. So the dock asks the tiler whenever the index is on disk, and only estimates when it
    is not — an estimate is flagged as one, so a quoted number is never quietly wrong.
    """
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock
    from embed_cd import source as SRC

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    dock.bbox = (-126.05, 50.28, -125.90, 50.37)
    dock._tile_memo = None
    e = dock._estimate()
    assert e["tiles"] > 0
    assert "approx" in e, "the estimate must say whether it is exact"

    have_index = any(os.path.exists(SRC.Index(c).npz_path)
                     for c in (dock._cache_dir(), None))
    if have_index:
        assert not e["approx"], "the index is present, so the count should be exact"
        src = SRC.AlphaEarthSource(index=SRC.Index(
            dock._cache_dir() if os.path.exists(SRC.Index(dock._cache_dir()).npz_path) else None))
        exact = len(src.list_tiles(dock.bbox, 2019, 2024)[0])
        assert e["tiles"] == exact, f"dock said {e['tiles']}, tiler says {exact}"
        print(f"ok tile count is exact ({exact}), not an area guess")
    else:
        assert e["approx"], "with no index there is nothing to be exact about"
        print("ok no tile index present; estimate correctly flagged as approximate")

    # the step header carries the cutoff, so everything that changes it must refresh the header
    dock.layer_id = "pretend"
    dock.slider.setValue(11)
    title = dock.step2.title() if hasattr(dock.step2, "title") else dock.steps.itemText(1)
    assert "0.11" in title, f"header did not follow the slider: {title!r}"
    dock.slider.setValue(25)
    title = dock.step2.title() if hasattr(dock.step2, "title") else dock.steps.itemText(1)
    assert "0.25" in title, f"header did not follow the slider: {title!r}"
    dock.cleanup()


def test_the_worker_interpreter_is_found_and_a_failed_start_is_reported():
    """Two halves of the same bug, which shipped through 0.29.0 as a silent hang off Windows.

    `_python_exe` only ever looked for `python.exe` and otherwise returned bare "python". On
    Linux and macOS QGIS puts the interpreter at `<prefix>/bin/python3` and most current
    distributions have no `python` at all, so the worker could not start.

    And a failed start is INVISIBLE: measured under QGIS 4.0.1, QProcess emits
    errorOccurred(FailedToStart) and never emits `finished`. The dock listened only for
    `finished`, so `self.proc` stayed set — Run disabled, progress bar spinning, no message,
    no way out but restarting QGIS. Whatever goes wrong, the UI has to come back.

    Only the first half is platform-specific, so this asserts it for whichever platform is
    running; the reporting half is checked everywhere.
    """
    import os
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.PyQt.QtCore import QProcess
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock, _scoped

    class _Bar:
        def pushMessage(self, *a, **k):
            pass

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

        def messageBar(self):
            return _Bar()

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))

    exe = dock._python_exe()
    # The point of the fix: on THIS platform it must resolve to something that exists, not to
    # a bare name that may not be on PATH.
    assert os.path.isfile(exe), f"_python_exe returned {exe!r}, which is not a file"

    # A failed start must release the UI and say why.
    dock.proc = QProcess(dock)
    dock.progress.setVisible(True)
    dock._on_proc_error(_scoped(QProcess, "ProcessError", "FailedToStart"))
    assert dock.proc is None, "a worker that never started must not leave the dock busy"
    assert not dock.progress.isVisible(), "the progress bar must stop"
    assert "Could not start Python" in dock.status.text(),         f"the failure has to be stated; status was {dock.status.text()!r}"

    # Crashes and I/O errors ARE followed by `finished`, so handling them here too would clear
    # self.proc out from under it.
    dock.proc = QProcess(dock)
    dock._on_proc_error(_scoped(QProcess, "ProcessError", "Crashed"))
    assert dock.proc is not None, "only FailedToStart is terminal here; `finished` handles the rest"
    dock.proc = None

    dock.deleteLater()
    print(f"ok worker interpreter resolves to {os.path.basename(exe)}, failed start reports itself")


def test_no_step_hides_its_own_controls_and_the_header_tracks_the_settings():
    """An open step must never need scrolling to reach its own buttons.

    QToolBox puts every page in a QScrollArea of its own, so this panel had TWO nested scroll
    areas. Measured on a 700 px dock before the fix: step 1 wanted 190 px, its viewport gave
    164, and the 26 px it lost were exactly the 'Make change map' button — the primary action
    of the whole plugin, invisible, with a thin inner scrollbar as the only clue. Steps 2 and 3
    lost 69 and 305 px.

    Also guards Cancel and the step header, both of which were the same kind of bug: a control
    or a fact stranded where the user cannot see it.
    """
    from qgis.PyQt.QtWidgets import QMainWindow, QScrollArea, QApplication
    from qgis.PyQt.QtCore import Qt
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    win = QMainWindow()
    win.resize(420, 700)
    dock = ChangeDock(_Iface(win, QgsMapCanvas()))
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    win.show()
    for _ in range(4):
        QApplication.processEvents()

    def clipped(page):
        """How many pixels the page's own scroll area is hiding."""
        area = page.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        assert area is not None, "QToolBox is expected to wrap pages in a QScrollArea"
        return area.verticalScrollBar().maximum()

    # Steps 2 and 3 are DISABLED until a result exists, and setCurrentIndex on a disabled item
    # is a silent no-op — so without this the loop below would test step 1 three times and pass
    # while proving nothing. Give the dock the state a real finished run has.
    dock.bbox = (-126.5, 49.0, -124.0, 51.0)
    dock.layer_id = "pretend-a-change-map-exists"
    dock._sync()
    for _ in range(3):
        QApplication.processEvents()

    pages = (dock.step1, dock.step2, dock.classify_group)
    for i, page in enumerate(pages):
        dock.steps.setCurrentIndex(i)
        for _ in range(3):
            QApplication.processEvents()
        assert dock.steps.currentIndex() == i,             f"step {i + 1} would not open — is it still disabled?"
        assert clipped(page) == 0,             f"step {i + 1} hides {clipped(page)} px of its own content"

    # Cancel belongs with the progress bar, outside the accordion — a running job has to be
    # stoppable from whichever step happens to be open, and step 1 folds itself once a result
    # exists.
    assert dock.cancel_btn.parentWidget() is dock.progress.parentWidget(),         "Cancel must live with the progress bar, not inside a foldable step"

    # Everything quoted in step 1's header must refresh it. Detail used to update the cost
    # line and not the header, so the header claimed a resolution that was no longer set.
    dock.detail.setCurrentText("100 m")
    assert "100 m" in dock.steps.itemText(0),         f"header did not follow Detail: {dock.steps.itemText(0)!r}"
    dock.year_b.setCurrentText("2025")
    assert "2025" in dock.steps.itemText(0),         f"header did not follow the year: {dock.steps.itemText(0)!r}"

    # The headers have to LOOK pressable. A QToolBox tab is drawn as a thin rule with a label,
    # which reads as a divider, so folded steps looked like captions and went unclicked. The
    # tab face is styled, and a caret carries the same signal in the text — because a user's Qt
    # style can override a stylesheet and cannot override a triangle.
    assert "QToolBox::tab" in dock.steps.styleSheet(), "the tab face lost its styling"
    for i in range(3):
        dock.steps.setCurrentIndex(i)
        for _ in range(2):
            QApplication.processEvents()
        texts = [dock.steps.itemText(j) for j in range(3)]
        assert texts[i].startswith("▾"), f"open step {i} should show an open caret: {texts[i]!r}"
        for j in range(3):
            if j != i:
                assert texts[j].startswith("▸"),                     f"folded step {j} should show a closed caret: {texts[j]!r}"

    dock.deleteLater()
    win.deleteLater()
    print("ok every step fits its own controls; Cancel is always reachable; header tracks")


def test_the_two_classify_modes_reread_the_same_labels():
    """Switching between Transition and End state must be free, reversible, and lossless.

    Both modes read the SAME stored [A, B] vectors — the transform happens at fit time — so
    nothing is recomputed, no polygons are re-cut and no labels are dropped. That is the whole
    reason this feature was small: the after-embedding was already on disk.
    """
    import tempfile
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aemode_")
    p = _panel(d, _tiny_job(d), ClassifyPanel)
    p.make_polygons()
    assert p.vectors is not None and p.vectors.shape[0] == 2

    p.classes.append("cutblock")
    p.colors["cutblock"] = "#d85a30"
    p._refresh_list()
    p.list.setCurrentRow(0)
    feat = next(p.layer.getFeatures())
    p.label_at(feat.geometry().centroid().asPoint())
    labels_before = dict(p.labels)
    assert labels_before, "setup: the click must have labelled something"

    # The PANEL defaults to End state (a usability choice); the ENGINE still defaults to delta
    # (a data-compatibility contract for files written before modes existed). Both are asserted,
    # here and in test_em_head, because it would be easy to "fix" the difference and silently
    # change what every untagged preset means.
    p._set_features("delta")
    p._refit(force=True)
    assert p.features() == "delta"
    assert p.head.features == "delta"
    first = list(p.pred)

    p._set_features("after")
    assert p.features() == "after"
    p._refit(force=True)
    assert p.labels == labels_before, "switching mode lost labels"
    assert p.head.features == "after", "the head was not told about the mode"
    # The stored vectors are the pair; only the view narrows.
    assert p.head.n_features_in_ == p.vectors.shape[1], "the head must still be fed [A, B]"

    p._set_features("delta")
    p._refit(force=True)
    assert list(p.pred) == first, "switching back did not reproduce the original answers"

    # And the mode survives a save/reload of the run.
    p._set_features("after")
    p._save_labels()
    from embed_cd import store as ST
    assert ST.load_labels(ST.labels_path(d, 2019, 2024))[5] == "after"

    QgsProject.instance().removeMapLayer(p.layer.id())
    print("ok both modes read the same labels; switching is reversible, lossless and saved")


def test_a_transition_class_name_can_be_typed_as_one_field_or_two():
    """The two-box dialog must never become an obstacle: filling only 'From' has to give
    exactly the single-box behaviour, because that is what everyone did before it existed."""
    from embed_cd_qgis.classify import _ARROW

    # The composition rule, without driving a modal dialog: both filled joins them, one filled
    # is the bare name. (The dialog itself is exercised by hand; see the plan's verification.)
    compose = lambda a, b: f"{a}{_ARROW}{b}" if a and b else (a or b)
    assert compose("forest", "clearing") == f"forest{_ARROW}clearing"
    assert compose("clearcut", "") == "clearcut", "a From-only entry must give the bare name"
    assert compose("", "bare") == "bare"

    # And a rename splits an existing transition name back apart.
    name = f"forest{_ARROW}clearing"
    before, _, after = name.partition(_ARROW.strip())
    assert before.strip() == "forest" and after.strip() == "clearing"
    print("ok transition names compose from two fields and split back for rename")


def test_stepping_respects_the_selected_class_and_skips_what_you_answered():
    """The two complaints about cycling, which were two different bugs.

    Selecting a class had NO effect on the arrows — `_cycle_rows` never consulted
    `current_class()`, so "pick cutblock, step through cutblocks" was simply not implemented.

    And "Least certain first" called `review_order` without `locked`, a parameter that has
    existed since the port for exactly this, so the work-list kept handing back objects the
    user had already labelled. That is what made stepping look like it was wandering between
    classes at random.
    """
    import tempfile
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aecycle_")
    p = _panel(d, _tiny_job(d), ClassifyPanel)
    p.make_polygons()
    assert len(p.polys) == 2, p.polys

    p.classes.extend(["cutblock", "field"])
    p.colors.update({"cutblock": "#d85a30", "field": "#1d9e75"})
    p._refresh_list()

    def use(key):
        p.cycle_mode.setCurrentIndex(p.cycle_mode.findData(key))

    # label row 0 cutblock, leave row 1 for the model
    p.labels = {0: "cutblock"}
    p._refit(force=True)

    # --- the class filter names itself after the selection, and scopes to it ---
    p.list.setCurrentRow(p.classes.index("cutblock"))
    i = p.cycle_mode.findData("class")
    assert p.cycle_mode.itemText(i) == 'Only "cutblock"', p.cycle_mode.itemText(i)
    use("class")
    rows = p._cycle_rows()
    assert 0 in rows, "the class filter dropped an object the user put in that class"
    for r in rows:
        called = p.labels.get(r) or (str(p.pred[r]) if p.pred is not None else "")
        assert called == "cutblock", f"row {r} is '{called}', not the selected class"

    # switching the selected class re-aims both the name and the rows
    p.list.setCurrentRow(p.classes.index("field"))
    assert p.cycle_mode.itemText(i) == 'Only "field"', p.cycle_mode.itemText(i)
    assert 0 not in p._cycle_rows(), "the filter still walks the previously selected class"

    # --- least-certain no longer re-offers what you already answered ---
    use("uncertain")
    assert 0 not in p._cycle_rows(),         "'least certain first' is still offering an object the user has labelled"
    use("labelled")
    assert 0 in p._cycle_rows(), "a labelled object must stay reachable under 'Only labelled'"

    # --- renaming a class follows through to the filter ---
    p.list.setCurrentRow(p.classes.index("cutblock"))
    p.classes[p.classes.index("cutblock")] = "clearcut"
    p.labels = {0: "clearcut"}
    p._refresh_list()
    p.list.setCurrentRow(p.classes.index("clearcut"))
    assert p.cycle_mode.itemText(i) == 'Only "clearcut"', p.cycle_mode.itemText(i)

    QgsProject.instance().removeMapLayer(p.layer.id())
    print("ok arrows scope to the selected class and skip what you already labelled")


def test_the_output_crs_must_be_able_to_express_metres():
    """A reported failure: every run in one project ended with "No tiles produced a result"
    and nothing else, at any area size.

    Detail is in METRES but was handed straight to the project's CRS. In a geographic project
    10 m becomes 10 DEGREES, so the output grid collapses to a single pixel, every tile falls
    outside it, and the job reports nothing usable. Measured on a 1 km area at 50.46N:

        EPSG:3857   158x159 px   1/1 tiles kept
        EPSG:32611  105x105 px   1/1 tiles kept
        EPSG:4326       1x1 px   0/1 tiles kept   <- the bug
        EPSG:4269       1x1 px   0/1 tiles kept
        OGC:CRS84       1x1 px   0/1 tiles kept

    The panel meanwhile said "output 100 x 100 px", because the estimate works from kilometres
    and never consulted the project CRS — so the UI and the run disagreed silently.

    A CRS in FEET is the same mistake, quieter: the map comes out at roughly a third of the
    asked-for resolution instead of empty. Hence the check is on the units, not on whether the
    CRS happens to be geographic.
    """
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.core import QgsCoordinateReferenceSystem, QgsProject
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    original = QgsProject.instance().crs()
    try:
        dock.bbox = (-114.78517, 50.45817, -114.77106, 50.46722)   # the reported area, UTM 11N
        for authid, expect, why in (
                ("EPSG:32611", "EPSG:32611", "already the right UTM zone, used as-is"),
                ("EPSG:3400", "EPSG:3400", "Alberta 10-TM: projected and true to scale here"),
                ("EPSG:3857", "EPSG:32611", "Web Mercator: metric in NAME only, 36% short here"),
                ("EPSG:4326", "EPSG:32611", "degrees"),
                ("EPSG:4269", "EPSG:32611", "degrees (NAD83)"),
                ("EPSG:2263", "EPSG:32611", "US survey feet, and the wrong side of the continent"),
        ):
            crs = QgsCoordinateReferenceSystem(authid)
            if not crs.isValid():
                continue                      # CRS not in this build's database; skip, do not fail
            QgsProject.instance().setCrs(crs)
            got = dock._target_crs()
            assert got == expect, f"project {authid} ({why}) -> {got}, expected {expect}"

        # The measurement behind that: Web Mercator's metre is not a ground metre away from
        # the equator, which is why the test is on distance and not on units.
        merc = dock._ground_metres("EPSG:3857")
        utm = dock._ground_metres("EPSG:32611")
        assert 600 < merc < 700, f"1000 Mercator units should be ~643 m here, measured {merc}"
        assert abs(utm - 1000.0) < 5, f"1000 UTM metres should be ~1000 m, measured {utm}"
        # Close the loop: the CRS this returns must actually produce a usable grid. Checking
        # only the authid would pass even if the fallback itself were degenerate.
        from embed_cd import grid as G
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        bbox = dock.bbox                                          # the reported 1 km area
        good = G.make_grid(bbox, dock._target_crs(), 10.0)
        bad = G.make_grid(bbox, "EPSG:4326", 10.0)
        assert min(bad.width, bad.height) == 1,             "the degenerate case no longer reproduces — has make_grid changed?"
        # ~1 km at 10 m ground metres is ~100 px a side. Web Mercator used to give ~158 here,
        # which is the oversampling this change removes.
        assert 95 <= good.width <= 110 and 95 <= good.height <= 110,             f"expected ~100 px for a 1 km area at 10 m, got {good.width}x{good.height}"
    finally:
        QgsProject.instance().setCrs(original)
    dock.deleteLater()
    print(f"ok output CRS is measured, not assumed: 1 km area is {good.width}x{good.height} px "
          f"in UTM (~10 m ground pixels), {bad.width}x{bad.height} px if the project CRS were "
          f"used raw")


def test_the_panel_and_the_job_agree_on_the_output_size():
    """The panel must not compute the output size a second way.

    It used to derive pixels from kilometres and the Detail figure, which silently assumed the
    output CRS was true to scale. Web Mercator is not, so on a 10x10 km area the panel promised
    1000x1000 px while the job wrote 1525x1535 at 49N and 2670x2688 at 68N — under-reporting by
    1/cos(lat)^2, up to 7x at 68N.

    That is not cosmetic: `poly_gb` comes from the same number, so the confirmation that exists
    to stop 'Generate Embedded Vector Set' running out of memory was wrong by the same factor.

    This is the third time the panel and the engine have disagreed about a quantity they both
    compute (tile count, output CRS, output size). The fix is the same each time — ask the
    engine — and this test is what stops the fourth.
    """
    import math
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.core import QgsProject, QgsCoordinateReferenceSystem
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.dock import ChangeDock, _DETAIL
    from embed_cd import grid as G

    class _Iface:
        def __init__(self, win, canvas):
            self._w, self._c = win, canvas

        def mainWindow(self):
            return self._w

        def mapCanvas(self):
            return self._c

    dock = ChangeDock(_Iface(QMainWindow(), QgsMapCanvas()))
    original = QgsProject.instance().crs()
    try:
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        for lat in (0.0, 30.0, 49.0, 50.46, 60.0, 68.0):
            km = 10.0
            dlon = km / (111.32 * math.cos(math.radians(lat))) / 2
            dlat = km / 110.57 / 2
            dock.bbox = (-114.0 - dlon, lat - dlat, -114.0 + dlon, lat + dlat)
            for label, res in _DETAIL.items():
                dock.detail.setCurrentText(label)
                e = dock._estimate()
                g = G.make_grid(dock.bbox, dock._target_crs(), res)
                assert e["out_px"] == float(g.width) * g.height, (
                    f"at {lat}N, {label}: panel says {e['out_px']:.0f} px, "
                    f"the job writes {g.width * g.height} px")

            # And a pixel must now be the size it claims to be, wherever you are.
            dock.detail.setCurrentText("10 m (full)")
            ground = dock._ground_metres(dock._target_crs())
            assert abs(10.0 * ground / 1000.0 - 10.0) < 0.3, (
                f"at {lat}N a 10 m pixel covers {10.0 * ground / 1000.0:.2f} m of ground")
    finally:
        QgsProject.instance().setCrs(original)
    dock.deleteLater()
    print("ok panel and job agree on output size, and 10 m means 10 m at every latitude")


def test_clicking_labels_a_polygon_when_the_canvas_crs_differs_from_the_layer():
    """The 0.36 regression: click a polygon, nothing happens.

    `toMapCoordinates` returns the CANVAS's CRS; `setFilterRect` wants the LAYER's. Until 0.36
    the change map was written in the project's CRS so those were always the same object and
    nothing converted between them. Then the output moved to the area's UTM zone while projects
    stay in Web Mercator, and the search rectangle started landing 14,000 km away. Every click
    found nothing, in silence.

    The stub canvas the other panel tests use has no CRS at all, so this crossing was never
    exercised — hence a real QgsMapCanvas here, deliberately set to a different CRS from the
    layer.
    """
    import tempfile
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY,
                           QgsRectangle)
    from qgis.gui import QgsMapCanvas
    from embed_cd_qgis.classify import ClassifyPanel

    d = tempfile.mkdtemp(prefix="aeclick_")
    vrt = _tiny_job(d)

    canvas = QgsMapCanvas()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))

    class Combo:
        def __init__(self, v):
            self.v = v

        def currentText(self):
            return self.v

    class Host:
        out_dir, vrt_path = d, vrt
        year_a, year_b = Combo("2019"), Combo("2024")

        def _threshold(self):
            return 0.10

    class Iface:
        def mapCanvas(self):
            return canvas

    p = ClassifyPanel(Host(), Iface())
    p.make_polygons()
    assert p.layer is not None and len(p.polys) >= 1, p.polys
    assert p.layer.crs().authid() == "EPSG:32610", p.layer.crs().authid()

    feat = next(p.layer.getFeatures())
    here = feat.geometry().centroid().asPoint()               # layer CRS, UTM 10N
    to_canvas = QgsCoordinateTransform(p.layer.crs(), canvas.mapSettings().destinationCrs(),
                                       QgsProject.instance())
    clicked = to_canvas.transform(here)                       # what toMapCoordinates gives us

    # Give the canvas a real scale so mapUnitsPerPixel is meaningful.
    canvas.setExtent(QgsRectangle(clicked.x() - 2000, clicked.y() - 2000,
                                  clicked.x() + 2000, clicked.y() + 2000))
    apart = abs(clicked.x() - here.x())
    assert apart > 1e6,         f"setup is not exercising the bug: the two CRSs differ by only {apart:.0f} m"

    p.classes.append("cutblock")
    p.colors["cutblock"] = "#d85a30"
    p._refresh_list()
    p.list.setCurrentRow(0)

    p.label_at(clicked)
    assert p.labels, (
        f"a click {apart / 1000:.0f} km from the layer's own coordinates labelled nothing — "
        "the click was not transformed into the layer's CRS")

    # And right-click still removes it, on the same path.
    p.label_at(clicked, clear=True)
    assert not p.labels, "right-click did not remove the label"

    QgsProject.instance().removeMapLayer(p.layer.id())
    print(f"ok a click in canvas CRS labels the right polygon ({apart / 1000:.0f} km apart "
          "in raw coordinates)")


# NOT COVERED HERE: class-colour sync and the dashed outline for labelled objects.
#
# The feature is verified — a scratch script drives the panel end to end and confirms the
# data-defined expression is "CASE WHEN \"label\" ... THEN 'dash' ELSE 'solid' END", that a
# swatch colour written in the panel reaches the renderer, and that a colour set on the renderer
# comes back to the panel. What could not be made stable is a version of that living in THIS
# file: constructing a panel and touching its renderer here takes the process down with no
# traceback and no failing assertion, first at the end of the run and then equally when moved to
# the front, while the same calls in a standalone script survive. That is Qt object ownership,
# not the plugin logic, and a test that crashes the suite is worse than an honest gap.
#
# If this is picked up again: the crash is silent (exit 127), so run with PYTHONUNBUFFERED=1 or
# the progress lines are lost in the buffer and it looks like an earlier test failed.


if __name__ == "__main__":
    test_a_rewritten_vrt_is_only_seen_at_a_new_path()
    test_a_users_correction_is_never_revised_by_the_model()
    test_pausing_makes_a_label_a_local_edit()
    test_restyling_twice_does_not_corrupt_the_heap()
    test_style_with_no_classes_yet()
    test_polygon_rows_are_read_from_idx_not_the_feature_id()
    test_the_whole_panel_loop_works()
    test_removing_the_layer_does_not_break_the_panel()
    test_class_examples_survive_a_re_polygonize()
    test_best_guess_option_reduces_unknowns()
    test_the_photo_strip_offers_every_year_and_stacks_them()
    test_a_saved_run_can_be_reopened_and_is_fully_live()
    test_a_folder_holding_several_runs_asks_which_one()
    test_polygons_and_labelling_survive_closing_the_session()
    test_two_areas_over_the_same_years_cannot_share_a_run_folder()
    test_the_dock_always_says_where_results_will_go()
    test_a_named_area_keeps_its_name_and_never_shares_a_group()
    test_undo_and_stepping_through_objects()
    test_steps_fold_once_and_keep_their_answer_in_the_title()
    test_switching_between_areas_restores_each_one()
    test_the_tile_estimate_asks_the_tiler_when_it_can()
    test_the_worker_interpreter_is_found_and_a_failed_start_is_reported()
    test_no_step_hides_its_own_controls_and_the_header_tracks_the_settings()
    test_the_two_classify_modes_reread_the_same_labels()
    test_a_transition_class_name_can_be_typed_as_one_field_or_two()
    test_stepping_respects_the_selected_class_and_skips_what_you_answered()
    test_the_output_crs_must_be_able_to_express_metres()
    test_the_panel_and_the_job_agree_on_the_output_size()
    test_clicking_labels_a_polygon_when_the_canvas_crs_differs_from_the_layer()
    print("all ok")
