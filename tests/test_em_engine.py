"""EMBED-CD engine self-check. Run: python tests/test_em_engine.py

Covers the three things that make a TILED change mosaic correct:
  1. scoring is absolute and nodata is a code (so tiles are comparable -> no seams)
  2. histograms accumulate across tiles so Otsu works without holding the mosaic
  3. every tile lands on one shared output grid, and the VRT stitches them
"""
import os
import tempfile

import numpy as np

from embed_cd import grid as G
from embed_cd import score as S
from embed_cd.vrt import write_vrt

_R = np.random.default_rng(5)
_D = _R.normal(0, 1, (3, 64))
_D /= np.linalg.norm(_D, axis=1, keepdims=True)
FOREST, BARE, CROP = _D


def _tile(seed=0, changed_rows=slice(0, 8)):
    """16x16x64 pair sharing per-pixel texture (as real years do), with a changed band."""
    rng = np.random.default_rng(seed)
    tex = rng.normal(0, 0.1, (16, 16, 64))
    a = (tex + rng.normal(0, 0.02, (16, 16, 64)) + CROP).astype(np.float32)
    b = (tex + rng.normal(0, 0.02, (16, 16, 64)) + CROP).astype(np.float32)
    a[changed_rows] = (tex[changed_rows] + FOREST).astype(np.float32)
    b[changed_rows] = (tex[changed_rows] + BARE).astype(np.float32)
    return a, b


def test_score_is_absolute_and_flags_nodata():
    a, b = _tile()
    a[12, :3] = 0.0            # missing in A
    b[13, :3] = np.nan         # missing in B
    a[14, :3] = 0.0
    b[14, :3] = 0.0            # missing in both
    s, cov = S.change_score(a, b)

    assert s.dtype == np.float32 and cov.dtype == np.uint8
    ok = s != S.NODATA
    assert s[ok].min() >= 0.0 and s[ok].max() <= 1.0, "absolute 0..1, never stretched"
    assert s[:8][s[:8] != S.NODATA].mean() > 0.2, "changed band scores high"
    assert s[9:12].mean() < 0.05, "unchanged rows score near zero"
    # nodata is a code, never 0 (0 would read as 'nothing changed')
    assert (cov[:8] == S.COV_OK).all(), "usable pixels are OK, never the no-tile sentinel"
    assert (s[12, :3] == S.NODATA).all() and (cov[12, :3] == S.COV_MISSING_A).all()
    assert (s[13, :3] == S.NODATA).all() and (cov[13, :3] == S.COV_MISSING_B).all()
    assert (s[14, :3] == S.NODATA).all() and (cov[14, :3] == S.COV_MISSING_BOTH).all()


def test_absolute_scoring_keeps_tiles_comparable():
    """The seam test: two tiles with very different amounts of change must still put the SAME
    physical change at the same score. Percentile-stretching per tile would break this."""
    a1, b1 = _tile(seed=1, changed_rows=slice(0, 2))     # barely any change
    a2, b2 = _tile(seed=1, changed_rows=slice(0, 14))    # mostly changed
    s1, _ = S.change_score(a1, b1)
    s2, _ = S.change_score(a2, b2)
    assert abs(float(s1[0].mean()) - float(s2[0].mean())) < 0.05, \
        "same change -> same score regardless of the rest of the tile"


def test_histograms_accumulate_and_otsu_splits():
    a, b = _tile(seed=2)
    s, _ = S.change_score(a, b)
    h1 = S.histogram(s)
    h2 = S.histogram(s)
    total = h1 + h2                                   # merging tiles is just addition
    assert total.sum() == 2 * (s != S.NODATA).sum()
    t = S.otsu_from_histogram(total)
    assert 0.0 < t < 1.0
    assert (s[:8] >= t).mean() > 0.9, "changed band kept"
    assert (s[9:] < t).mean() > 0.9, "unchanged dropped"
    frac = S.fraction_above(total, t)
    assert 0.2 < frac < 0.8, f"reported changed fraction {frac} is plausible"


def test_grid_and_windows_tile_the_output_exactly():
    bbox = (-123.4, 48.5, -123.3, 48.6)
    g = G.make_grid(bbox, "EPSG:3857", 10.0)
    assert g.width > 0 and g.height > 0
    assert g.x0 % 10.0 == 0 and g.y0 % 10.0 == 0, "origin snapped to the resolution"

    # a sub-box inside the AOI maps to a window strictly inside the grid
    win = G.window_for_bounds(g, (-123.38, 48.52, -123.34, 48.56), "EPSG:4326")
    r0, r1, c0, c1 = win
    assert 0 <= r0 < r1 <= g.height and 0 <= c0 < c1 <= g.width
    # a box far away doesn't produce a bogus window
    assert G.window_for_bounds(g, (10.0, 10.0, 10.1, 10.1), "EPSG:4326") is None

    # the window transform must agree with the global grid at that offset
    t = G.transform_of(g, r0, c0)
    assert abs(t.c - (g.x0 + c0 * g.res)) < 1e-6
    assert abs(t.f - (g.y0 - r0 * g.res)) < 1e-6
    assert abs(t.a - g.res) < 1e-9


def test_vrt_stitches_tiles_and_is_readable():
    from embed_cd import gdalio as GD

    d = tempfile.mkdtemp(prefix="tc_vrt_")
    g = G.Grid("EPSG:3857", 0.0, 100.0, 10.0, 20, 10)
    tiles = []
    for i, (r0, c0) in enumerate([(0, 0), (0, 10)]):     # two side-by-side 10x10 tiles
        p = os.path.join(d, f"t{i}.tif")
        score = np.full((10, 10), 0.1 * (i + 1), dtype=np.float32)
        cov = np.zeros((10, 10), dtype=np.uint8)
        GD.write(p, np.stack([score, cov.astype("float32")]), g.crs,
                 G.transform_of(g, r0, c0), nodata=S.NODATA)
        tiles.append({"path": p, "row0": r0, "col0": c0, "width": 10, "height": 10})

    vp = write_vrt(os.path.join(d, "mosaic.vrt"), g, tiles)
    arr, _crs, _tr = GD.read(vp, band=1)
    assert arr.shape == (10, 20)
    assert np.allclose(arr[:, :10], 0.1) and np.allclose(arr[:, 10:], 0.2), \
        "each tile lands in its own window with no offset"


def test_an_empty_overlapping_tile_cannot_erase_a_neighbours_coverage():
    """AlphaEarth publishes per UTM zone, so an area near a zone boundary fetches tiles from
    BOTH zones and each is entirely EMPTY over the other's half. An empty tile reports class 4,
    which is a real value — source nodata only protects 0 — so whichever sorts last used to
    paint its emptiness over the other's real data. Measured at -125.98 (the 9N/10N line):
    42% of the mosaic said 'no data in either year' while every one of those pixels carried a
    valid change value. The change band never suffered because its fill IS its nodata.

    The rule this locks in: wherever there is a change value, coverage must say COV_OK.
    """
    from embed_cd import gdalio as GD

    d = tempfile.mkdtemp(prefix="tc_zone_")
    g = G.Grid("EPSG:3857", 0.0, 100.0, 10.0, 10, 10)
    tiles = []
    # Same window, both tiles. "good" holds real data; "empty" overlaps it with nothing at all
    # and is written SECOND so it paints last — the losing order.
    for name, score_val, cov_val in (("good", 0.42, S.COV_OK),
                                     ("empty", S.NODATA, S.COV_MISSING_BOTH)):
        p = os.path.join(d, f"{name}.tif")
        GD.write(p, np.stack([np.full((10, 10), score_val, dtype="float32"),
                              np.full((10, 10), cov_val, dtype="float32")]),
                 g.crs, G.transform_of(g, 0, 0), nodata=S.NODATA)
        tiles.append({"path": p, "row0": 0, "col0": 0, "width": 10, "height": 10})

    vp = write_vrt(os.path.join(d, "zones.vrt"), g, tiles)
    chg, _, _ = GD.read(vp, band=1)
    cov, _, _ = GD.read(vp, band=2)
    assert np.allclose(chg, 0.42), "the change band was already safe; keep it that way"
    bad = (chg != S.NODATA) & (cov != S.COV_OK)
    assert not bad.any(), (
        f"{bad.sum()} px carry a change value while coverage claims no data — the empty tile "
        f"clobbered its neighbour (coverage reads {np.unique(cov)})")
    print("ok an empty overlapping tile no longer erases coverage")


def test_coverage_still_reports_why_when_no_tile_has_data():
    """The override forces COV_OK from the change band, so check it does not also flatten the
    genuine diagnosis: where NOTHING has an answer, 'no data in 2019' must survive."""
    from embed_cd import gdalio as GD

    d = tempfile.mkdtemp(prefix="tc_why_")
    g = G.Grid("EPSG:3857", 0.0, 100.0, 10.0, 10, 10)
    p = os.path.join(d, "a.tif")
    GD.write(p, np.stack([np.full((10, 10), S.NODATA, dtype="float32"),
                          np.full((10, 10), S.COV_MISSING_A, dtype="float32")]),
             g.crs, G.transform_of(g, 0, 0), nodata=S.NODATA)
    vp = write_vrt(os.path.join(d, "why.vrt"), g,
                   [{"path": p, "row0": 0, "col0": 0, "width": 10, "height": 10}])
    cov, _, _ = GD.read(vp, band=2)
    assert (cov == S.COV_MISSING_A).all(), f"lost the reason, got {np.unique(cov)}"
    print("ok coverage still explains itself where there is no answer")


if __name__ == "__main__":
    test_score_is_absolute_and_flags_nodata()
    test_absolute_scoring_keeps_tiles_comparable()
    test_histograms_accumulate_and_otsu_splits()
    test_grid_and_windows_tile_the_output_exactly()
    test_vrt_stitches_tiles_and_is_readable()
    test_an_empty_overlapping_tile_cannot_erase_a_neighbours_coverage()
    test_coverage_still_reports_why_when_no_tile_has_data()
    print("ok")
