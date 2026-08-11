"""Polygonize + attach: the half that runs after the job, on files already on disk.

The invariant that makes deferred geometry sound is the last test here — a polygon spanning two
tiles must get the EXACT count-weighted mean of its pixels. If that drifts, every vector in a
multi-tile job is subtly wrong and nothing downstream can tell.
Run: python tests/test_em_objects.py
"""
import os
import tempfile

import numpy as np
from embed_me import gdalio as GD

from embed_me import cells as CE, objects as OB, score as S

CRS = "EPSG:32610"
RES = 10.0
X0, Y0 = 500000.0, 5400000.0        # top-left


def _write_change(path, arr):
    return GD.write(path, arr, CRS, GD.Transform.from_origin(X0, Y0, RES, RES),
                    nodata=S.NODATA)


def _write_cells_for(d, ma, mb, n, smean, smax, west, north, cell_px=16, ya=2019, yb=2024):
    tr = GD.Transform.from_origin(west, north, RES, RES)
    name = (f"cells_{ya}-{yb}_{cell_px * RES:g}m_{CRS.replace(':', '')}_"
            f"{int(west)}_{int(north - ma.shape[0] * cell_px * RES)}.tif")
    return CE.write_cells(os.path.join(d, name), ma, mb, n, smean, smax, CRS, tr, cell_px)


def test_polygonize_finds_regions_and_respects_min_area():
    with tempfile.TemporaryDirectory() as d:
        arr = np.zeros((64, 64), np.float32)
        arr[8:24, 8:24] = 0.20          # 16x16 px = 2.56 ha
        arr[40:44, 40:44] = 0.30        # 4x4 px = 0.16 ha, should be filtered
        p = _write_change(os.path.join(d, "c.tif"), arr)

        polys, crs = OB.polygonize(p, 0.1, min_area_ha=1.0)
        assert len(polys) == 1, [q["area_ha"] for q in polys]
        assert np.isclose(polys[0]["area_ha"], 2.56), polys[0]["area_ha"]
        assert np.isclose(polys[0]["chg_mean"], 0.20, atol=1e-5)
        assert polys[0]["n_px"] == 256
        assert str(crs) == CRS

        polys, _ = OB.polygonize(p, 0.1, min_area_ha=0.1)
        assert len(polys) == 2, "the small region appears once min_area allows it"
        print("ok polygonize finds regions, stats are exact, min-area filters")


def test_raising_the_threshold_splits_one_object_into_several():
    """The measured behaviour that ruled out capture-time geometry: objects are not nested."""
    with tempfile.TemporaryDirectory() as d:
        arr = np.zeros((64, 64), np.float32)
        arr[8:40, 8:40] = 0.05          # a broad weak region
        arr[10:18, 10:18] = 0.40        # two strong cores inside it
        arr[30:38, 30:38] = 0.40
        p = _write_change(os.path.join(d, "c.tif"), arr)

        low, _ = OB.polygonize(p, 0.02, min_area_ha=0.1)
        high, _ = OB.polygonize(p, 0.20, min_area_ha=0.1)
        assert len(low) == 1 and len(high) == 2, (len(low), len(high))
        assert low[0]["area_ha"] > sum(q["area_ha"] for q in high)
        print(f"ok one object at t=0.02 becomes {len(high)} at t=0.20 — not nested")


def test_vector_is_the_count_weighted_mean_of_covered_cells():
    with tempfile.TemporaryDirectory() as d:
        arr = np.zeros((64, 64), np.float32)
        arr[0:32, 0:32] = 0.5
        p = _write_change(os.path.join(d, "c.tif"), arr)

        depth = 4
        ma = np.zeros((4, 4, depth), np.float32)
        mb = np.zeros((4, 4, depth), np.float32)
        n = np.full((4, 4), 256.0, np.float32)
        ma[:2, :2] = 1.0                       # the four cells under the change region
        mb[:2, :2] = 2.0
        ma[2:, :] = 9.0                        # cells outside it must not contribute
        smean = np.full((4, 4), 0.5, np.float32)
        _write_cells_for(d, ma, mb, n, smean, smean, X0, Y0)

        polys, crs = OB.polygonize(p, 0.1, min_area_ha=0.1)
        assert len(polys) == 1
        idx = OB.CellIndex(d, 2019, 2024, 160)
        assert idx, "cell files should be discovered"
        vec = OB.attach_vectors(polys, idx, str(crs))[0]
        assert vec.shape == (2 * depth,)
        assert np.allclose(vec[:depth], 1.0), vec[:depth]
        assert np.allclose(vec[depth:], 2.0), vec[depth:]
        print("ok a polygon's vector is the mean of the cells it covers, and only those")


def test_unequal_counts_are_weighted_not_averaged():
    """Two cells, one full and one a quarter full. The mean must be weighted 256:64, not 1:1 —
    the difference is exactly what an unweighted average of cell means would get wrong."""
    with tempfile.TemporaryDirectory() as d:
        arr = np.zeros((32, 64), np.float32)
        arr[0:16, 0:32] = 0.5
        p = _write_change(os.path.join(d, "c.tif"), arr)

        depth = 2
        ma = np.zeros((2, 4, depth), np.float32)
        mb = np.zeros((2, 4, depth), np.float32)
        n = np.zeros((2, 4), np.float32)
        ma[0, 0] = 0.0
        ma[0, 1] = 4.0
        n[0, 0], n[0, 1] = 256.0, 64.0
        smean = np.full((2, 4), 0.5, np.float32)
        _write_cells_for(d, ma, mb, n, smean, smean, X0, Y0)

        polys, crs = OB.polygonize(p, 0.1, min_area_ha=0.1)
        vec = OB.attach_vectors(polys, OB.CellIndex(d, 2019, 2024, 160), str(crs))[0]
        expect = (0.0 * 256 + 4.0 * 64) / (256 + 64)          # = 0.8, not 2.0
        assert np.allclose(vec[:depth], expect), (vec[:depth], expect)
        print(f"ok cells are count-weighted ({expect:.2f}), not averaged flat (2.00)")


def test_pool_cutoff_selects_high_change_cells_and_never_blanks_a_polygon():
    with tempfile.TemporaryDirectory() as d:
        arr = np.zeros((64, 64), np.float32)
        arr[0:32, 0:32] = 0.5
        p = _write_change(os.path.join(d, "c.tif"), arr)

        depth = 2
        ma = np.zeros((4, 4, depth), np.float32)
        mb = np.zeros((4, 4, depth), np.float32)
        n = np.full((4, 4), 256.0, np.float32)
        smean = np.zeros((4, 4), np.float32)
        ma[0, 0] = 10.0
        smean[0, 0] = 0.9                     # one strong cell
        ma[0, 1] = ma[1, 0] = ma[1, 1] = 1.0
        smean[0, 1] = smean[1, 0] = smean[1, 1] = 0.1
        _write_cells_for(d, ma, mb, n, smean, smean, X0, Y0)

        polys, crs = OB.polygonize(p, 0.1, min_area_ha=0.1)
        idx = OB.CellIndex(d, 2019, 2024, 160)
        loose = OB.attach_vectors(polys, idx, str(crs))[0][:depth]
        tight = OB.attach_vectors(polys, idx, str(crs), pool_cutoff=0.5)[0][:depth]
        assert np.allclose(loose, (10.0 + 3 * 1.0) / 4), loose
        assert np.allclose(tight, 10.0), tight
        # a cutoff nothing clears must fall back, not zero the polygon out
        impossible = OB.attach_vectors(polys, idx, str(crs), pool_cutoff=99.0)[0][:depth]
        assert np.allclose(impossible, loose), impossible
        print("ok pool cutoff selects high-change cells and never blanks a polygon")


def test_vector_blob_roundtrips_exactly():
    v = np.array([0.5, -0.25, 1e-6, 0.0], np.float32)
    assert np.array_equal(OB.unpack_vec(OB.pack_vec(v)), v)
    assert len(OB.pack_vec(np.zeros(128, np.float32))) == 512
    print("ok vector packs to 512 bytes and round-trips bit-exact")


if __name__ == "__main__":
    test_polygonize_finds_regions_and_respects_min_area()
    test_raising_the_threshold_splits_one_object_into_several()
    test_vector_is_the_count_weighted_mean_of_covered_cells()
    test_unequal_counts_are_weighted_not_averaged()
    test_pool_cutoff_selects_high_change_cells_and_never_blanks_a_polygon()
    test_vector_blob_roundtrips_exactly()
    print("all ok")
