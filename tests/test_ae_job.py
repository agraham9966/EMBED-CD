"""Tiled-job integration check with a FAKE AlphaEarth source (no network).

Verifies the things that only show up once tiles are streamed together:
  - every tile lands in the right place in one seamless mosaic
  - the mosaic is readable through the VRT while tiles are still arriving
  - a re-run skips finished tiles (resumable) and still reports them
  - cancelling stops early without corrupting the output
  - a tile that fails to read doesn't kill the job
Run: python tests/test_ae_job.py
"""
import os
import tempfile

import numpy as np
from alphaearth_change.gdalio import Transform, transform_bounds


def from_origin(x, y, xr, yr):
    return Transform.from_origin(x, y, xr, yr)


def _read(path, *bands):
    """(band, ...) as arrays — the tests only ever want the pixels."""
    from alphaearth_change import gdalio as GD
    return tuple(GD.read(path, band=b)[0] for b in bands)

from alphaearth_change import job, score as S, vrt
from alphaearth_change.source import Tile

CRS = "EPSG:32610"
TILE_PX = 100
RES = 100.0                        # 100 px x 100 m = 10 km tiles, like a real 1024 px @ 10 m one
SIDE = TILE_PX * RES
# Deliberately OFF the UTM central meridian (500000). On it, a UTM rectangle warps to a plain
# rectangle in EPSG:3857 and adjacent tile windows abut perfectly — which hides every seam bug.
# Real tiles are tens of km off it, where meridian convergence makes the windows overlap.
EASTING = 400000.0

_R = np.random.default_rng(3)
_D = _R.normal(0, 1, (3, 64))       # AlphaEarth embeddings are 64-d
_D /= np.linalg.norm(_D, axis=1, keepdims=True)
FOREST, BARE, CROP = _D

# Two tiles side by side on the source's own UTM grid, exactly as the real source emits them.
WEST = Tile(CRS, EASTING, 5400000.0, EASTING + SIDE, 5400000.0 + SIDE)
EAST = Tile(CRS, EASTING + SIDE, 5400000.0, EASTING + 2 * SIDE, 5400000.0 + SIDE)
TILES = [WEST, EAST]
BBOX = transform_bounds(CRS, "EPSG:4326",
                        WEST.west, WEST.south, EAST.east, EAST.north)


class FakeAlphaEarth:
    """Two adjacent 1 km tiles. The WEST tile changes (forest->bare) in its top half; the EAST
    tile doesn't change at all. `bad` tiles raise on read; `missing_in_b` tiles have no COG for
    the later year (which the real source reports as a None result, not an exception)."""

    def __init__(self, tiles, bad=(), missing_in_b=()):
        self._tiles = list(tiles)
        self._bad = set(bad)
        self._missing_b = set(missing_in_b)
        self.fetches = []

    def list_tiles(self, bbox, year_a, year_b):
        a = set(self._tiles)
        b = {t for t in self._tiles if t not in self._missing_b}
        return sorted(a | b), sorted(a & b), sorted(a ^ b)

    def fetch(self, tile, year):
        if tile in self._bad:
            raise RuntimeError("simulated read failure")
        if tile in self._missing_b and year == 2024:
            return None, None, None            # no COG for this year here
        self.fetches.append((tile, year))
        rng = np.random.default_rng(int(tile.west) + int(tile.south))
        tex = rng.normal(0, 0.05, (TILE_PX, TILE_PX, 64))
        arr = (tex + CROP).astype(np.float32)
        if tile is WEST or tile == WEST:                   # the west tile has real change
            arr[:TILE_PX // 2] = (tex[:TILE_PX // 2] +
                                  (FOREST if year == 2019 else BARE)).astype(np.float32)
        # The transform is AUTHORITATIVE — job.py must place by this, never by the tile id.
        transform = from_origin(tile.west, tile.north, RES, RES)
        return arr, tile.crs, transform


def _run(out_dir, src, **kw):
    return job.run(BBOX, 2019, 2024, out_dir, dst_crs="EPSG:3857", res_m=RES, src=src, **kw)


def test_tiles_stream_into_one_seamless_mosaic():
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth(TILES)
    seen = []
    g, recs, hist, partial = _run(d, src, on_tile=lambda *a: seen.append(a[0]))

    assert len(recs) == 2 and seen == [1, 2], "both tiles processed, reported as they land"
    assert all(os.path.exists(r["path"]) for r in recs)
    assert not any(p.endswith(".part") for p in os.listdir(d)), "no partial files left behind"

    vp = vrt.write_vrt(os.path.join(d, "m.vrt"), g, recs)
    (arr,) = _read(vp, 1)
    assert arr.shape == (g.height, g.width)
    ok = arr != S.NODATA
    assert ok.mean() > 0.5, "mosaic is mostly filled"
    # the west tile's changed half should be the high-scoring part of the mosaic
    west = arr[:, :g.width // 2]
    east = arr[:, g.width // 2:]
    assert np.nanmax(west[west != S.NODATA]) > 0.2, "real change shows up"
    east_ok = east[east != S.NODATA]
    assert east_ok.size and east_ok.mean() < 0.1, "the unchanged tile stays low"
    assert hist.sum() > 0


def test_rerun_is_resumable():
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth(TILES)
    _run(d, src)
    n_first = len(src.fetches)
    src2 = FakeAlphaEarth(TILES)
    g, recs, hist, _ = _run(d, src2)         # same folder again
    assert len(recs) == 2, "already-finished tiles are still reported"
    assert hist.sum() > 0, "histogram rebuilt from the existing files"
    assert n_first > 0 and not src2.fetches, "a resumed run re-reads nothing"


def test_changing_detail_does_not_reuse_stale_tiles():
    """Re-running the same years into the same folder at a different Detail must NOT resume
    from the old tiles. They were built for a different output grid, so they carry the old
    pixel size: reused, each one is placed several times too large and smeared across the
    mosaic. It fails silently, which is what makes it dangerous."""
    d = tempfile.mkdtemp(prefix="aejob_")
    g1, r1, _, _ = _run(d, FakeAlphaEarth(TILES))                       # RES (=100 m)
    assert r1

    src2 = FakeAlphaEarth(TILES)
    g2, r2, _, _ = job.run(BBOX, 2019, 2024, d, dst_crs="EPSG:3857", res_m=RES * 3, src=src2)
    assert g2.width < g1.width, "coarser Detail must give a smaller grid"
    assert src2.fetches, "tiles for a different grid must be rebuilt, not resumed"
    for r in r2:
        assert r["col0"] + r["width"] <= g2.width, (
            f"tile {r['width']}px wide at col {r['col0']} overflows a {g2.width}px grid")
        assert r["row0"] + r["height"] <= g2.height

    vp = vrt.write_vrt(os.path.join(d, "m.vrt"), g2, r2)
    sc, cov = _read(vp, 1, 2)
    assert (cov[sc != S.NODATA] != S.COV_NO_TILE).all()
    print("ok changing Detail rebuilds instead of reusing tiles from another grid")


def test_cancel_stops_early():
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth(TILES)
    g, recs, hist, _ = _run(d, src, should_stop=lambda: True)
    assert len(recs) == 0, "cancelled before doing work"
    assert not [p for p in os.listdir(d) if p.endswith(".tif")]


def test_one_bad_tile_does_not_kill_the_job():
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth(TILES, bad=[WEST])
    g, recs, hist, _ = _run(d, src)
    assert len(recs) == 1, "the good tile still produced a result"


def test_partial_coverage_still_produces_a_map_with_a_nodata_class():
    """Partial coverage must NOT block the job. The tile missing a year still produces a
    result whose coverage band records which year is absent, so the map shows 'no data'
    rather than a hole that looks like 'nothing changed'."""
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth(TILES, missing_in_b=[EAST])
    g, recs, hist, partial = _run(d, src)

    assert len(recs) == 2, "every tile in the area is processed, not just the complete ones"
    assert EAST in partial, "the one-year-only tile is reported as partial"

    by_col = {r["col0"]: r["path"] for r in recs}
    east = by_col[max(by_col)]                     # the tile whose 'to' year is missing
    sc, cov = _read(east, 1, 2)
    assert (sc == S.NODATA).all(), "no change score is invented where a year is missing"
    assert (cov == S.COV_MISSING_B).any(), "the coverage band says WHICH year was missing"

    west = by_col[min(by_col)]                     # the complete tile still has real change
    (wsc,) = _read(west, 1)
    assert (wsc != S.NODATA).any() and np.nanmax(wsc[wsc != S.NODATA]) > 0.2


def test_every_unscored_pixel_is_explained_by_the_coverage_band():
    """The trustworthiness invariant: a user must never have to wonder whether an empty area
    means 'nothing changed' or 'we had no data'. So no pixel may be both unscored and marked
    'data in both years', and grid cells no tile reached must read NO_TILE (not OK) — GDAL
    fills uncovered VRT areas with 0, which is why 0 has to mean 'nothing here'."""
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth(TILES, missing_in_b=[EAST])
    g, recs, hist, partial = _run(d, src)
    vp = vrt.write_vrt(os.path.join(d, "m.vrt"), g, recs)
    sc, cov = _read(vp, 1, 2)

    assert (cov[sc == S.NODATA] != S.COV_OK).all(),         "an unscored pixel is never labelled 'data in both years'"
    assert (sc[cov == S.COV_OK] != S.NODATA).all(),         "a pixel labelled OK always carries a real score"
    # Tile windows OVERLAP (a UTM rectangle warps to a curved quad, and the window is its
    # bbox), so each tile's reprojection edge-fill lands on top of a neighbour's real data. If
    # the coverage band's VRT sources don't declare that fill transparent, the fill wins and
    # paints "no tile" straight through good data — a grey grid over the whole mosaic.
    assert (cov[sc != S.NODATA] != S.COV_NO_TILE).all(),         "a pixel WITH a score is never labelled 'not covered'"
    assert (cov == S.COV_NO_TILE).any(), "area outside the tiles reads as 'no tile'"
    assert (cov == S.COV_MISSING_B).any(), "the missing year is named"
    assert set(np.unique(cov)) <= {S.COV_NO_TILE, S.COV_OK, S.COV_MISSING_A,
                                   S.COV_MISSING_B, S.COV_MISSING_BOTH}


def test_placement_uses_real_tile_bounds_no_gaps():
    """Two adjacent tiles must produce ONE contiguous covered region. Placement reads each
    fetched tile's own transform and `window_for_bounds` ROUNDS every edge, so neighbours land
    on the same column and abut exactly — a floor/ceil there leaves a 1-px seam, and trusting
    the tile id instead of the transform leaves the half-tile grid-of-gaps."""
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth(TILES)
    g, recs, hist, partial = _run(d, src)
    vp = vrt.write_vrt(os.path.join(d, "m.vrt"), g, recs)
    (cov,) = _read(vp, 2)
    covered = (cov == S.COV_OK)
    assert covered.mean() > 0.9, f"tiles should cover the AOI, got {covered.mean():.2f}"
    # no full-height 'no tile' strip between the two adjacent tiles (ignore 1-px edges)
    interior = cov[:, 20:-20]
    assert not (interior == S.COV_NO_TILE).all(axis=0).any(), "no 'no tile' seam between tiles"


def test_no_tiles_at_all_is_the_only_hard_failure():
    d = tempfile.mkdtemp(prefix="aejob_")
    src = FakeAlphaEarth([])
    g, recs, hist, partial = _run(d, src)
    assert recs == [] and not partial


if __name__ == "__main__":
    test_tiles_stream_into_one_seamless_mosaic()
    test_rerun_is_resumable()
    test_changing_detail_does_not_reuse_stale_tiles()
    test_cancel_stops_early()
    test_one_bad_tile_does_not_kill_the_job()
    test_partial_coverage_still_produces_a_map_with_a_nodata_class()
    test_every_unscored_pixel_is_explained_by_the_coverage_band()
    test_placement_uses_real_tile_bounds_no_gaps()
    test_no_tiles_at_all_is_the_only_hard_failure()
    print("ok")
