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
from alphaearth_change import gdalio as GD

from alphaearth_change import score as S
from alphaearth_change.source import AlphaEarthSource, Index, Tile, _dequantize

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
    from alphaearth_change.source import _vsicurl
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
    print("all ok")
