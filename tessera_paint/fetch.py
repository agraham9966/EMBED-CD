"""Fetch a TESSERA embedding mosaic for a bbox. Thin wrapper over geotessera.

geotessera.fetch_mosaic_for_region already downloads, caches, stitches, and reprojects
tiles to one CRS, so all we add is the coverage + tile-count guards the plugin needs.
No QGIS here.
"""
from __future__ import annotations

import os

import numpy as np

# Per-user, writable tile cache. Without an explicit dir geotessera resolves a relative
# path against the process CWD, which inside QGIS is read-only Program Files -> WinError 5.
_DEFAULT_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "geotessera", "embeddings")


class NoCoverage(Exception):
    """TESSERA has no tile for this area/year."""
    def __init__(self, bbox, year, available=None):
        avail = (f" Years available here: {', '.join(map(str, available))}." if available
                 else " No TESSERA years cover this area.")
        super().__init__(f"No TESSERA tile for {year} in this region.{avail}")
        self.bbox, self.year, self.available = bbox, year, available


class AoiTooLarge(Exception):
    """AOI spans more tiles than the cap (guards RAM: mosaic is float32, ~620 MB/tile)."""
    def __init__(self, count, cap):
        super().__init__(f"AOI needs {count} tiles (cap {cap}); zoom in or raise the cap.")
        self.count, self.cap = count, cap


def load_region(bbox, year=2024, max_tiles=2, target_crs="EPSG:4326",
                cache_dir=None, progress_callback=None, downsample=1):
    """Return (mosaic float32 [H,W,128], rasterio transform, crs str) for bbox=(min_lon,min_lat,max_lon,max_lat).

    downsample>1 average-pools the mosaic by that factor (e.g. 5 -> ~50 m pixels) so large
    regions fit in memory. Raises NoCoverage if the area/year has no tiles, AoiTooLarge if it
    exceeds max_tiles. The count check hits only the (small, cached) registry — no tile bytes.
    """
    from geotessera import GeoTessera  # imported lazily so importing this module is cheap

    if cache_dir is None:
        cache_dir = _DEFAULT_CACHE
    os.makedirs(cache_dir, exist_ok=True)
    gt = GeoTessera(embeddings_dir=cache_dir)
    n = gt.embeddings_count(bbox, year=year)
    if n <= 0:
        avail = [y for y in range(2017, 2026) if gt.embeddings_count(bbox, year=y) > 0]
        raise NoCoverage(bbox, year, avail)
    if n > max_tiles:
        raise AoiTooLarge(n, max_tiles)
    # Warm the tile cache with parallel downloads (network-bound) before the merge;
    # fetch_mosaic_for_region then reads from cache and re-fetches only any stragglers.
    tiles = gt.registry.load_blocks_for_region(bounds=bbox, year=year)
    _parallel_download(gt, tiles, progress_callback)   # real 0->N progress
    if progress_callback:
        progress_callback(0, 0, "building mosaic")     # total=0 -> dock shows a busy bar
    # no progress_callback here: its internal phases each reset 0->100 and made the bar cycle
    mosaic, transform, crs = gt.fetch_mosaic_for_region(
        bbox, year=year, target_crs=target_crs,
    )
    # geotessera returns whole tiles (~11 km) that intersect the bbox, so the mosaic
    # overshoots the requested region. Clip to the bbox so data == the region asked for.
    mosaic, transform = _crop_to_bbox(mosaic, transform, crs, bbox)
    mosaic, transform = _downsample(mosaic, transform, downsample)
    return mosaic, transform, crs


def _downsample(mosaic, transform, factor):
    """Average-pool [H,W,C] by an integer factor, ignoring nodata (NaN). Returns
    (pooled, scaled_transform). No-op for factor <= 1."""
    import warnings
    from rasterio.transform import Affine

    factor = int(factor)
    if factor <= 1:
        return mosaic, transform
    h, w, c = mosaic.shape
    H, W = h // factor, w // factor
    if H == 0 or W == 0:
        return mosaic, transform
    blocks = mosaic[:H * factor, :W * factor].reshape(H, factor, W, factor, c)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)   # all-NaN blocks -> NaN
        pooled = np.nanmean(blocks, axis=(1, 3)).astype(np.float32)
    return pooled, transform * Affine.scale(factor, factor)


def _parallel_download(gt, tiles, progress_callback=None, workers=4):
    """Download tiles concurrently (I/O-bound) to warm the cache. Failures are ignored —
    the subsequent fetch_mosaic_for_region(auto_download=True) retries anything missing."""
    from concurrent.futures import ThreadPoolExecutor
    if not tiles:
        return

    def _dl(t):
        try:
            gt.download_tile(t[1], t[2], t[0])   # tile = (year, lon, lat)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=min(workers, len(tiles))) as ex:
        for i, _ in enumerate(ex.map(_dl, tiles), 1):
            if progress_callback:
                progress_callback(i, len(tiles), "downloading")


def _crop_to_bbox(mosaic, transform, crs, bbox_4326):
    """Crop [H,W,C] mosaic to bbox (min_lon,min_lat,max_lon,max_lat, EPSG:4326).
    Returns (cropped, new_transform). No-op if the crop would be empty."""
    from rasterio.warp import transform_bounds
    from rasterio.transform import rowcol, Affine

    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox_4326)
    r_top, c_left = rowcol(transform, left, top)
    r_bot, c_right = rowcol(transform, right, bottom)
    r0, r1 = sorted((int(r_top), int(r_bot)))
    c0, c1 = sorted((int(c_left), int(c_right)))
    h, w = mosaic.shape[:2]
    r0, c0 = max(0, r0), max(0, c0)
    r1, c1 = min(h, r1 + 1), min(w, c1 + 1)
    if r1 <= r0 or c1 <= c0:
        return mosaic, transform
    return mosaic[r0:r1, c0:c1], transform * Affine.translation(c0, r0)


if __name__ == "__main__":
    # ponytail: optional live smoke test (downloads ~150 MB, needs internet). Not a unit test.
    #   Cambridge UK, a known-covered area from geotessera docs.
    bbox = (0.10, 52.18, 0.16, 52.22)
    mosaic, transform, crs = load_region(bbox, year=2024, max_tiles=4)
    print("mosaic", mosaic.shape, mosaic.dtype, "crs", crs)
