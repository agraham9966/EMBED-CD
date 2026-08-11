"""UI-layer checks that only a real QGIS process can make. Skipped in the dev venv.

These exist because a whole class of PyQGIS bug is invisible to the normal suite: passing a C++
object that Qt takes OWNERSHIP of to two owners. It doesn't raise, it doesn't fail a render — it
corrupts the heap and takes QGIS down later, when the old object is destroyed. No traceback, so
nothing in a normal test would ever see it.

Run:  "C:\\Program Files\\QGIS 4.0.1\\bin\\python-qgis.bat" tests/test_ae_qgis_ui.py
"""
import os
import sys

try:
    from qgis.core import (QgsApplication, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
                           QgsProject, QgsMapSettings, QgsMapRendererParallelJob)
    from qgis.PyQt.QtCore import QSize
except ImportError:
    print("skipped: needs QGIS's own Python (python-qgis.bat)")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "plugin"))

QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", ""), True)
_app = QgsApplication([], False)
_app.initQgis()

from alphaearth_change_qgis.classify import ClassifyPanel, UNKNOWN     # noqa: E402


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
    from alphaearth_change import cells as CE, gdalio as GD

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
    CE.write_cells(os.path.join(d, "cells_2019-2024_16px_EPSG32610_500000_5399360.tif"),
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
    from alphaearth_change_qgis.classify import ClassifyPanel

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
    from alphaearth_change_qgis.classify import ClassifyPanel

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
    from alphaearth_change_qgis.classify import ClassifyPanel

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
    from alphaearth_change_qgis.classify import ClassifyPanel

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
    from alphaearth_change_qgis.classify import ClassifyPanel

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
    from alphaearth_change_qgis.classify import ClassifyPanel
    from alphaearth_change import head as H

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
    from alphaearth_change import gdalio as GD, vrt as V, grid as G

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
    print("all ok")
