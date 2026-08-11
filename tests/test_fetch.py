"""Crop-to-bbox self-check (no network). Run: python tests/test_fetch.py"""
import numpy as np
from rasterio.transform import from_origin

from tessera_paint.fetch import _crop_to_bbox, _downsample


def test_crop_to_bbox_windows_correctly():
    # 10x10x4 mosaic covering lon 0..1, lat 0..1 at 0.1deg pixels, in EPSG:4326 so no reproject
    mosaic = np.arange(10 * 10 * 4, dtype=np.float32).reshape(10, 10, 4)
    transform = from_origin(0.0, 1.0, 0.1, 0.1)   # west=0, north=1, 0.1deg pixels
    bbox = (0.25, 0.25, 0.55, 0.65)               # a sub-window

    cropped, t = _crop_to_bbox(mosaic, transform, "EPSG:4326", bbox)
    h, w = cropped.shape[:2]
    assert 3 <= h <= 5 and 3 <= w <= 5, f"cropped to a sub-window, got {h}x{w}"
    assert h < 10 and w < 10, "must be smaller than the full mosaic"
    # new transform origin should sit at the crop's top-left, inside the bbox lat/lon span
    assert 0.2 <= t.c <= 0.3, f"crop west edge near bbox min_lon, got {t.c}"
    assert 0.6 <= t.f <= 0.7, f"crop north edge near bbox max_lat, got {t.f}"


def test_crop_empty_is_noop():
    mosaic = np.zeros((5, 5, 2), dtype=np.float32)
    transform = from_origin(0.0, 1.0, 0.1, 0.1)
    # bbox entirely outside the mosaic -> degenerate -> returns original
    out, t = _crop_to_bbox(mosaic, transform, "EPSG:4326", (5.0, 5.0, 6.0, 6.0))
    assert out.shape == mosaic.shape


def test_downsample_pools_and_scales_transform():
    from rasterio.transform import from_origin
    mosaic = np.ones((8, 8, 3), dtype=np.float32)
    mosaic[0:2, 0:2, :] = 5.0            # a 2x2 block -> one pooled pixel = 5
    mosaic[6, 6, :] = np.nan             # nodata in a block; other 3 cells = 1 -> mean 1 (nan ignored)
    transform = from_origin(0.0, 1.0, 0.1, 0.1)
    pooled, t = _downsample(mosaic, transform, 2)
    assert pooled.shape == (4, 4, 3), "halved spatial dims"
    assert np.allclose(pooled[0, 0], 5.0), "2x2 block averaged"
    assert np.allclose(pooled[3, 3], 1.0), "NaN ignored in partial-nodata block"
    assert abs(t.a - 0.2) < 1e-9, "pixel size scaled by factor"


def test_downsample_noop():
    from rasterio.transform import from_origin
    m = np.ones((4, 4, 2), dtype=np.float32)
    out, t = _downsample(m, from_origin(0, 1, 0.1, 0.1), 1)
    assert out.shape == m.shape


if __name__ == "__main__":
    test_crop_to_bbox_windows_correctly()
    test_crop_empty_is_noop()
    test_downsample_pools_and_scales_transform()
    test_downsample_noop()
    print("ok")
