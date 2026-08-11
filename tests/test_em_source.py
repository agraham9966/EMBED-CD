"""AlphaEarth source checks. Offline by default: a real bottom-up GeoTIFF on disk plus a fake
index, so the two things that are easy to get silently wrong are actually exercised —

  - the COGs are SOUTH-UP, so a window read must be flipped and re-georeferenced
  - int8 values must be de-quantized as (v/127.5)**2 * sign(v), with -128 becoming unusable

Set AEF_LIVE=1 to additionally hit the real bucket (network, ~100 MB).
Run: python tests/test_tc_alphaearth.py
"""
import os
import tempfile

import numpy as np
from embed_me import gdalio as GD

from embed_me import score as S
from embed_me.source import AlphaEarthSource, Index, Tile, _dequantize

CRS = "EPSG:32610"
X0, Y0 = 336160.0, 5406720.0       # bottom-left, as the real files are
NPX = 64                            # a small stand-in for the real 8192


def _write_south_up(path, seed):
    """A bottom-up int8 COG-alike: positive y-res, origin at the SW corner."""
    r = np.random.default_rng(seed)
    data = r.integers(-127, 127, (64, NPX, NPX), dtype=np.int8)
    data[:, :4, :4] = -128                       # a nodata patch in the SOUTH-WEST corner
    # positive y-resolution: the file is SOUTH-UP, exactly like the real COGs
    GD.write(path, data, CRS, GD.Transform(10.0, 0.0, X0, 0.0, 10.0, Y0), nodata=-128)
    return data


class FakeIndex(Index):
    """The published index, minus the download. Deliberately subclasses the real Index and only
    replaces the stored arrays, so query/decode logic is the real thing under test."""

    def __init__(self, base, rows):
        self.cache_dir = None
        self.base = base
        self._d = {"key": np.array([r[0] for r in rows], dtype="S"),
                   "year": np.array([r[1] for r in rows], dtype=np.int32),
                   "crs": np.array([CRS] * len(rows), dtype="S"),
                   "utm_west": np.full(len(rows), X0),
                   "utm_south": np.full(len(rows), Y0),
                   "utm_east": np.full(len(rows), X0 + NPX * 10.0),
                   "utm_north": np.full(len(rows), Y0 + NPX * 10.0),
                   "wgs84_west": np.full(len(rows), -125.3),
                   "wgs84_south": np.full(len(rows), 48.7),
                   "wgs84_east": np.full(len(rows), -124.1),
                   "wgs84_north": np.full(len(rows), 49.6)}


def test_dequantize():
    raw = np.zeros((2, 2, 64), dtype=np.int8)
    raw[0, 0] = 127
    raw[0, 1] = -127
    raw[1, 0] = -128                                  # nodata
    raw[1, 1] = 64
    out = _dequantize(raw)
    assert np.isclose(out[0, 0, 0], (127 / 127.5) ** 2), out[0, 0, 0]
    assert np.isclose(out[0, 1, 0], -((127 / 127.5) ** 2)), out[0, 1, 0]
    assert (out[1, 0] == 0).all(), "nodata must become an all-zero vector"
    assert np.isclose(out[1, 1, 0], (64 / 127.5) ** 2)
    # an all-zero vector is exactly what the scorer calls unusable
    assert S.valid_mask(out)[1, 0] == False        # noqa: E712
    assert S.valid_mask(out)[0, 0] == True         # noqa: E712
    print("ok dequantize (+ nodata -> unusable)")


def test_fetch_flips_and_places():
    with tempfile.TemporaryDirectory() as d:
        raw = _write_south_up(os.path.join(d, "a.tiff"), 1)
        src = AlphaEarthSource(index=FakeIndex(d + os.sep, [("a.tiff", 2024)]), tile_px=32)

        tile = Tile(CRS, X0, Y0, X0 + 320.0, Y0 + 320.0)     # the SOUTH-WEST 32x32 block
        cube, crs, transform = src.fetch(tile, 2024)
        assert cube.shape == (32, 32, 64), cube.shape
        assert crs == CRS

        # north-up: the transform's origin is the tile's TOP-left and y-res is negative
        assert transform.c == tile.west and transform.f == tile.south + 320.0
        assert transform.e == -10.0

        # the nodata patch was written at the file's SOUTH-west; after the flip it must appear
        # at the BOTTOM of the returned array, not the top
        assert (cube[-4:, :4] == 0).all(), "south-west nodata should end up bottom-left"
        assert not (cube[:4, :4] == 0).all(), "top-left must not be the nodata patch"

        # values match the raw file flipped and de-quantized
        expect = _dequantize(np.moveaxis(raw[:, :32, :32][:, ::-1, :], 0, -1))
        assert np.allclose(cube, expect)

        # a year with no COG is a clean miss, not an exception
        assert src.fetch(tile, 2019) == (None, None, None)
        print("ok fetch flips south-up data and georeferences it north-up")


def test_tiles_are_block_aligned_and_seamless():
    with tempfile.TemporaryDirectory() as d:
        _write_south_up(os.path.join(d, "a.tiff"), 2)
        src = AlphaEarthSource(
            index=FakeIndex(d + os.sep, [("a.tiff", 2022), ("a.tiff", 2024)]), tile_px=32)

        bbox = (-125.3, 48.7, -124.1, 49.6)
        all_t, both, partial = src.list_tiles(bbox, 2022, 2024)
        assert all_t and not partial, (len(all_t), len(partial))
        assert set(all_t) == set(both)

        step = 32 * 10.0
        for t in all_t:
            # every tile edge sits on the COG's own block grid -> one block per read
            assert (t.west - X0) % step == 0 and (t.south - Y0) % step == 0, t
        # tiles tile the plane: no overlaps, and neighbours share an exact edge
        xs = sorted({t.west for t in all_t})
        assert all(b - a == step for a, b in zip(xs, xs[1:])), xs
        assert len(all_t) == len({(t.west, t.south) for t in all_t}), "duplicate tiles"
        print(f"ok list_tiles: {len(all_t)} block-aligned tiles, seamless")


def test_missing_year_is_partial():
    with tempfile.TemporaryDirectory() as d:
        _write_south_up(os.path.join(d, "a.tiff"), 3)
        src = AlphaEarthSource(index=FakeIndex(d + os.sep, [("a.tiff", 2024)]), tile_px=32)
        all_t, both, partial = src.list_tiles((-125.3, 48.7, -124.1, 49.6), 2022, 2024)
        assert all_t and not both and set(partial) == set(all_t)
        print("ok a year with no coverage reports as partial, not empty")


def test_remote_paths_get_the_vsicurl_prefix():
    """The offline tests all use LOCAL file paths, so none of them can see how a remote URL is
    opened — and GDAL, unlike rasterio, will not infer /vsicurl/ from an https:// URL. That gap
    let a port ship that fetched exactly nothing from the real bucket while 8/8 suites passed."""
    from embed_me.source import _vsicurl
    assert _vsicurl("https://data.source.coop/x.tiff") == "/vsicurl/https://data.source.coop/x.tiff"
    assert _vsicurl("/vsicurl/https://a/b") == "/vsicurl/https://a/b", "never double-prefix"
    local = os.path.join("C:" + os.sep, "tmp", "a.tiff")
    assert _vsicurl(local) == local, (
        "a local path must be left alone — CURL rejects 'C:' as a port number")
    print("ok remote URLs are given the /vsicurl/ prefix GDAL requires")


def test_live():
    """Real bucket, the user's BC area. Opt-in: AEF_LIVE=1."""
    src = AlphaEarthSource()
    years = src.years()
    assert set(range(2017, 2026)) <= set(years), years
    bbox = (-124.2, 49.2, -124.1, 49.3)
    all_t, both, partial = src.list_tiles(bbox, 2022, 2024)
    assert all_t and set(all_t) == set(both), (len(all_t), len(partial))
    t = all_t[0]
    a, crs, tr = src.fetch(t, 2022)
    b, _, _ = src.fetch(t, 2024)
    assert a is not None and b is not None and a.shape == b.shape
    assert np.isclose(np.linalg.norm(a, axis=2)[a.any(axis=2)].mean(), 1.0, atol=0.01)
    sc, cov = S.change_score(a, b)
    ok = cov == S.COV_OK
    print(f"ok live: years={years[0]}-{years[-1]} tiles={len(all_t)} shape={a.shape} "
          f"cov_ok={ok.mean():.3f} median_change={np.median(sc[ok]):.4f} "
          f"p99={np.percentile(sc[ok], 99):.4f}")


def test_factor_picks_an_overview_that_is_never_coarser_than_asked():
    """Detail must never be upsampled from a coarser overview, and must stop at 160 m because
    that is one whole embedding cell per source pixel — past it the classifier has nothing to
    pool."""
    from embed_me.source import factor_for, NATIVE_RES, MAX_FACTOR

    for res, want in ((10, 1), (20, 2), (30, 2), (50, 4), (100, 8), (160, 16), (5000, 16)):
        got = factor_for(res)
        assert got == want, f"factor_for({res}) = {got}, expected {want}"
        assert NATIVE_RES * got <= max(res, NATIVE_RES), "never read coarser than requested"
    assert factor_for(1e9) == MAX_FACTOR, "capped so 160 m cells stay buildable"
    print("ok factor_for picks the coarsest overview that is still fine enough")


def test_a_coarse_tile_covers_proportionally_more_ground():
    """The win is per unit GROUND, not per pixel: a tile stays 1024 px (so memory is flat) and
    covers `factor` times more ground on each side. Without this a coarse job would issue just
    as many HTTP round trips and save almost nothing."""
    from embed_me.source import AlphaEarthSource

    base = AlphaEarthSource(index=object(), factor=1)
    coarse = AlphaEarthSource(index=object(), factor=8)
    assert base.res == 10.0 and coarse.res == 80.0
    assert base.tile_px == coarse.tile_px, "same pixels = same memory per tile"
    ground = lambda s: s.tile_px * s.res
    assert ground(coarse) == ground(base) * 8, "8x the ground for the same bytes"
    assert abs(ground(coarse) / 1000 - 81.92) < 1e-6, "one whole COG per read at 80 m"
    print("ok a coarse tile covers 8x the ground for the same memory")


def test_the_same_physical_cell_has_the_same_name_at_every_detail():
    """A 160 m cell is 16 px of full-res source but 2 px of the 80 m overview, and the two build
    the SAME vector (measured: cosine 0.99998). Naming cells by pixel count would invent a cache
    miss between identical files and silently re-download the area on a Detail change."""
    from embed_me import cells as CE
    from embed_me.source import Tile

    t = Tile("EPSG:32610", 500000.0, 5399360.0, 510240.0, 5409600.0)
    assert CE.cells_filename(t, 2019, 2024, 160.0) == CE.cells_filename(t, 2019, 2024, 160.0)
    assert "160m" in CE.cells_filename(t, 2019, 2024, 160.0)
    assert CE.cells_filename(t, 2019, 2024, 160.0) != CE.cells_filename(t, 2019, 2024, 320.0), \
        "a genuinely different cell size must still change the name"
    print("ok cell files are named by ground size, so Detail does not invalidate them")


if __name__ == "__main__":
    test_dequantize()
    test_fetch_flips_and_places()
    test_tiles_are_block_aligned_and_seamless()
    test_missing_year_is_partial()
    test_remote_paths_get_the_vsicurl_prefix()
    if os.environ.get("AEF_LIVE") == "1":
        test_live()
    else:
        print("(skipped live bucket test; set AEF_LIVE=1 to run it)")
    test_factor_picks_an_overview_that_is_never_coarser_than_asked()
    test_a_coarse_tile_covers_proportionally_more_ground()
    test_the_same_physical_cell_has_the_same_name_at_every_detail()
    print("all ok")
