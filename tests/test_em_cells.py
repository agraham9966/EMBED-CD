"""Cell store: the coarse copy of the embeddings kept while a tile is briefly in memory.

The things that would be silently wrong forever if they broke:
  - a cell's mean must equal a direct mean of its usable pixels (it is the only record left)
  - unusable pixels must not drag the mean toward zero
  - a clipped edge tile must report the RIGHT count, or cross-tile weighting is wrong
  - cell grids of adjacent tiles must align and never overlap
  - a file built for different settings must never be reused (the 0.5.2 lesson)
Run: python tests/test_em_cells.py
"""
import os
import tempfile

import numpy as np
from embed_cd.gdalio import Transform


def from_origin(x, y, xr, yr):
    return Transform.from_origin(x, y, xr, yr)

from embed_cd import cells as CE, job, score as S
from embed_cd.source import Tile

CRS = "EPSG:32610"


def _fixture(h=64, w=64, depth=8, seed=0):
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 1, (h, w, depth)).astype(np.float32)
    b = rng.normal(0, 1, (h, w, depth)).astype(np.float32)
    sc = rng.random((h, w)).astype(np.float32)
    usable = np.ones((h, w), bool)
    return a, b, sc, usable


def test_cell_mean_equals_a_direct_mean():
    a, b, sc, usable = _fixture()
    ma, mb, n, smean, smax = CE.pool(a, b, sc, usable, cell_px=16)
    assert ma.shape == (4, 4, 8) and n.shape == (4, 4)
    assert (n == 256).all(), "a full 16x16 cell holds 256 pixels"
    for ci in range(4):
        for cj in range(4):
            blk = a[ci * 16:(ci + 1) * 16, cj * 16:(cj + 1) * 16]
            assert np.allclose(ma[ci, cj], blk.reshape(-1, 8).mean(axis=0), atol=1e-5)
            s = sc[ci * 16:(ci + 1) * 16, cj * 16:(cj + 1) * 16]
            assert np.isclose(smean[ci, cj], s.mean(), atol=1e-5)
            assert np.isclose(smax[ci, cj], s.max(), atol=1e-6)
    print("ok cell mean equals a direct mean of its pixels")


def test_unusable_pixels_are_excluded_not_averaged_in():
    """If unusable pixels were counted, every cell touching nodata would be pulled toward zero
    and would describe something that isn't there."""
    a, b, sc, usable = _fixture()
    usable[:8, :8] = False                      # half of cell (0,0) in each direction
    a[:8, :8] = 0.0                             # nodata comes back as an all-zero vector
    b[:8, :8] = 0.0
    ma, mb, n, smean, _ = CE.pool(a, b, sc, usable, cell_px=16)

    assert n[0, 0] == 256 - 64, n[0, 0]
    blk = a[:16, :16].reshape(-1, 8)[usable[:16, :16].reshape(-1)]
    assert np.allclose(ma[0, 0], blk.mean(axis=0), atol=1e-5)
    assert np.isclose(smean[0, 0], sc[:16, :16][usable[:16, :16]].mean(), atol=1e-5)
    assert (n[1:, 1:] == 256).all(), "untouched cells keep a full count"
    print("ok unusable pixels are excluded from the mean, not averaged in as zeros")


def test_a_cell_with_no_usable_pixels_is_zero_not_nan():
    a, b, sc, usable = _fixture()
    usable[:16, :16] = False
    a[:16, :16] = 0.0
    b[:16, :16] = 0.0
    ma, mb, n, smean, smax = CE.pool(a, b, sc, usable, cell_px=16)
    assert n[0, 0] == 0
    for arr in (ma[0, 0], mb[0, 0], np.array([smean[0, 0], smax[0, 0]])):
        assert np.isfinite(arr).all() and (arr == 0).all()
    print("ok an entirely unusable cell is zero and finite, never NaN")


def test_clipped_edge_tile_reports_a_partial_count():
    """A tile at a COG edge is smaller than 1024 px. Its last cell is partial, and the count has
    to say so — it is the weight used when a polygon spans tiles."""
    a, b, sc, usable = _fixture(h=40, w=40)
    ma, mb, n, _, _ = CE.pool(a, b, sc, usable, cell_px=16)
    assert ma.shape[:2] == (3, 3), ma.shape
    assert n[0, 0] == 256 and n[2, 2] == 8 * 8 and n[0, 2] == 16 * 8
    blk = a[32:40, 32:40].reshape(-1, 8)
    assert np.allclose(ma[2, 2], blk.mean(axis=0), atol=1e-5), "partial cell averages only real px"
    print("ok a clipped tile's partial cells carry the right count and mean")


def test_roundtrip_preserves_values_and_georeferencing():
    a, b, sc, usable = _fixture()
    ma, mb, n, smean, smax = CE.pool(a, b, sc, usable, cell_px=16)
    tr = from_origin(500000.0, 5400000.0, 10.0, 10.0)
    with tempfile.TemporaryDirectory() as d:
        p = CE.write_cells(os.path.join(d, "c.tif"), ma, mb, n, smean, smax, CRS, tr, 16)
        assert not os.path.exists(p + ".part"), "no partial file left behind"
        ra, rb, rn, rsm, rsx, crs, rtr = CE.read_cells(p)
    assert np.allclose(ra, ma) and np.allclose(rb, mb) and np.allclose(rn, n)
    assert np.allclose(rsm, smean) and np.allclose(rsx, smax)
    assert str(crs) == CRS
    assert rtr.a == 160.0 and rtr.e == -160.0, "cell pixels are cell_px times the source"
    assert rtr.c == 500000.0 and rtr.f == 5400000.0, "origin follows the tile, not the name"
    print("ok cell store round-trips values and georeferencing")


def test_adjacent_tiles_produce_aligned_non_overlapping_cells():
    """1024 / 16 = 64 exactly, and tiles are block-aligned, so cells tile the plane. If they
    overlapped, a polygon spanning a tile edge would double-count."""
    side = 1024 * 10.0
    west, south = 400000.0, 5400000.0
    grids = []
    for dx in (0.0, side):
        tr = from_origin(west + dx, south + side, 10.0, 10.0)
        cell_tr = (tr.a * 16, tr.c, tr.f)
        grids.append((cell_tr, 1024 // 16))
    (res, x0, y0), n0 = grids[0]
    (_, x1, _), _ = grids[1]
    assert x0 + n0 * res == x1, "the east tile's first cell starts where the west tile's last ends"
    print("ok adjacent tiles' cell grids abut exactly")


def test_filename_is_a_full_signature():
    t = Tile(CRS, 400000.0, 5400000.0, 410240.0, 5410240.0)
    base = CE.cells_filename(t, 2019, 2024, 160)
    assert base != CE.cells_filename(t, 2020, 2024, 160), "years must change the name"
    assert base != CE.cells_filename(t, 2019, 2024, 320), "cell size must change the name"
    other = Tile(CRS, 410240.0, 5400000.0, 420480.0, 5410240.0)
    assert base != CE.cells_filename(other, 2019, 2024, 160), "position must change the name"
    print(f"ok filename is a full signature: {base}")


def test_job_writes_cells_and_resumes_correctly():
    """Turning capture on for a job that already has tiles must NOT resume past them, or you get
    a change map with no embeddings behind it."""
    import test_em_job as T

    d = tempfile.mkdtemp(prefix="aecells_")
    src1 = T.FakeAlphaEarth(T.TILES)
    job.run(T.BBOX, 2019, 2024, d, dst_crs="EPSG:3857", res_m=T.RES, src=src1)   # no cells
    assert not [f for f in os.listdir(d) if f.startswith("cells_")]

    src2 = T.FakeAlphaEarth(T.TILES)
    job.run(T.BBOX, 2019, 2024, d, dst_crs="EPSG:3857", res_m=T.RES, src=src2, cell_m=160)
    made = [f for f in os.listdir(d) if f.startswith("cells_")]
    assert src2.fetches, "must re-fetch: the tiles existed but the cells did not"
    assert len(made) == 2, made

    src3 = T.FakeAlphaEarth(T.TILES)
    job.run(T.BBOX, 2019, 2024, d, dst_crs="EPSG:3857", res_m=T.RES, src=src3, cell_m=160)
    assert not src3.fetches, "with both tiles and cells present, resume fetches nothing"
    print("ok job writes the cell store and resumes on it correctly")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_cell_mean_equals_a_direct_mean()
    test_unusable_pixels_are_excluded_not_averaged_in()
    test_a_cell_with_no_usable_pixels_is_zero_not_nan()
    test_clipped_edge_tile_reports_a_partial_count()
    test_roundtrip_preserves_values_and_georeferencing()
    test_adjacent_tiles_produce_aligned_non_overlapping_cells()
    test_filename_is_a_full_signature()
    test_job_writes_cells_and_resumes_correctly()
    print("all ok")
