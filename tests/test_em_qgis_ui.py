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
    loaded, _ = H.load_classes(preset)
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
    print("all ok")
