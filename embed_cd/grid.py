"""One global output grid for the whole job, and each tile's exact window in it.

Why a single predefined grid: every tile is reprojected INTO its own window of this grid, so
all tile files share an origin and resolution. That makes the mosaic seamless and lets a VRT
stitch them with no resampling or feathering. (Reprojecting each tile independently to "some"
grid would leave sub-pixel offsets between neighbours and visible seams.)

Only the 1-band result is ever reprojected — never the 128-band embeddings. That is where most
of the speed comes from.
"""
import math
from collections import namedtuple

Grid = namedtuple("Grid", "crs x0 y0 res width height")   # x0,y0 = top-left corner


def make_grid(bbox_4326, dst_crs, res_m):
    """Output grid covering an EPSG:4326 bbox, snapped to a whole multiple of the resolution."""
    from .gdalio import transform_bounds

    left, bottom, right, top = transform_bounds("EPSG:4326", dst_crs, *bbox_4326)
    x0 = math.floor(left / res_m) * res_m
    y0 = math.ceil(top / res_m) * res_m
    width = max(1, int(math.ceil((right - x0) / res_m)))
    height = max(1, int(math.ceil((y0 - bottom) / res_m)))
    return Grid(str(dst_crs), x0, y0, float(res_m), width, height)


def transform_of(grid, row0=0, col0=0):
    from .gdalio import Transform
    return Transform.from_origin(grid.x0 + col0 * grid.res, grid.y0 - row0 * grid.res,
                                 grid.res, grid.res)


def window_for_bounds(grid, bounds, src_crs):
    """The (row0, row1, col0, col1) window of `grid` covered by `bounds` (given in src_crs).
    Returns None when the bounds fall outside the grid."""
    from .gdalio import transform_bounds

    left, bottom, right, top = transform_bounds(src_crs, grid.crs, *bounds)
    # ROUND every edge (not floor/ceil): two tiles that share a world edge then round it to the
    # SAME column, so adjacent tiles abut exactly — no 1-px overlap (which caused a coverage
    # seam) and no 1-px gap.
    col0 = int(round((left - grid.x0) / grid.res))
    col1 = int(round((right - grid.x0) / grid.res))
    row0 = int(round((grid.y0 - top) / grid.res))
    row1 = int(round((grid.y0 - bottom) / grid.res))
    col0, row0 = max(0, col0), max(0, row0)
    col1, row1 = min(grid.width, col1), min(grid.height, row1)
    if col1 <= col0 or row1 <= row0:
        return None
    return row0, row1, col0, col1


def estimate(grid, n_tiles, bytes_per_tile_download=67e6):
    # 67 MB = one AlphaEarth tile-year: 1024 x 1024 px x 64 int8 channels.
    """Up-front honesty for the user: output size and download volume before committing."""
    px = grid.width * grid.height
    return {
        "px": px,
        "output_bytes": int(px * (4 + 1)), # float32 score + uint8 coverage
        "download_bytes": int(n_tiles * 2 * bytes_per_tile_download),
        "tiles": n_tiles,
    }
