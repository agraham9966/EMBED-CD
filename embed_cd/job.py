"""The tiled change job: stream tiles, emit results as they land.

Per tile: read year A and year B -> score on the tile's native grid (both years share it,
so no alignment needed) -> reproject ONLY the 1-band result into this job's output grid ->
write a small 2-band GeoTIFF -> free the embeddings. Peak memory is one tile pair regardless
of how large the area is.

Reads run on a small thread pool (pure I/O) while the main thread does the numpy/GDAL
work — that keeps PROJ off worker threads, which is what crashes inside QGIS.

The source is AlphaEarth (see `source.py`). A tile is a block-aligned window of one of its
COGs, identified by UTM bounds; both years share that grid, which is the invariant the whole
"score on the native grid, reproject only the result" design rests on.
"""
import os

import numpy as np

from . import grid as G
from . import score as S
from .cells import CELL_M as CE_CELL_M

def open_source(cache_dir=None, res_m=None):
    """`res_m` is the OUTPUT resolution being asked for. It picks which built-in overview to
    read, so a coarse job stops paying full-resolution download for detail it will throw away."""
    from .source import AlphaEarthSource, factor_for
    return AlphaEarthSource(cache_dir, factor=1 if res_m is None else factor_for(res_m))


def list_tiles(src, bbox, year_a, year_b):
    """(all, both, partial) tiles in the area.

    Every tile present in EITHER year is processed. A tile missing one year still produces a
    result — an all-nodata score plus a coverage band saying which year is absent — so the map
    shows "no data here" instead of a silent hole, and partial coverage never blocks the job.
    """
    return src.list_tiles(bbox, year_a, year_b)


def _reproject_into(arr, src_crs, src_transform, dst_crs, dst_transform, shape, nodata, nearest):
    from . import gdalio as GD
    return GD.reproject_into(arr, src_crs, src_transform, dst_crs, dst_transform, shape,
                             nodata, nearest=nearest)


def _try_fetch(src, tile, year):
    try:
        arr, crs, transform = src.fetch(tile, year)
        if arr is None:
            return None, None, None                # no COG for this year here
        return np.asarray(arr, np.float32), crs, transform
    except Exception:
        return None, None, None                    # unreadable — treated as a missing year


def _record_for_existing(path, out_grid):
    """Placement of an already-written tile, read from the FILE's own transform (not the name),
    so resumed jobs land tiles exactly where they were first written.

    Returns None if the file doesn't belong to this grid, which makes the caller rebuild it.
    The filename already encodes the grid, so this should be unreachable — it's here because
    the failure it guards against is silent and severe: a tile carrying a different pixel size
    keeps its OWN width/height, so it gets placed several times too large and smeared across
    the mosaic instead of erroring."""
    from . import gdalio as GD
    arr, _crs, t = GD.read(path, band=1)
    if abs(abs(t.a) - out_grid.res) > 1e-6 or abs(abs(t.e) - out_grid.res) > 1e-6:
        return None
    col0 = int(round((t.c - out_grid.x0) / out_grid.res))
    row0 = int(round((out_grid.y0 - t.f) / out_grid.res))
    return {"path": path, "row0": row0, "col0": col0,
            "width": arr.shape[1], "height": arr.shape[0], "hist": S.histogram(arr)}


def tile_filename(tile, year_a, year_b, out_grid):
    """Stable per-tile name — and it MUST name every input that changes the file's CONTENT.

    Encoding only the tile's position is not enough: a re-run at a different Detail, or with
    the project in a different CRS, then finds a "finished" tile built for a completely
    different output grid and reuses it. The stale tile keeps its old pixel size, so it is
    placed at the wrong scale and spills across the mosaic — silently, because resuming is
    supposed to be a no-op. Resumability is only safe when the name is a full job signature.
    """
    grid = f"{out_grid.crs.replace(':', '')}-{out_grid.res:g}m"
    return (f"tile_{year_a}-{year_b}_{grid}_"
            f"{tile.crs.replace(':', '')}_{int(tile.west)}_{int(tile.south)}.tif")


def _cells_done(out_dir, tile, year_a, year_b, cell_px, cell_m):
    """A finished tile is only a reason to skip the fetch if the cell store is finished too —
    otherwise turning capture on for an existing job would resume past every tile and produce
    a change map with no embeddings behind it."""
    if not cell_px:
        return True
    from . import cells as CE
    return os.path.exists(os.path.join(out_dir, CE.cells_filename(tile, year_a, year_b, cell_m)))


def _write_cells(a, b, sc, cov, crs, transform, tile, year_a, year_b, out_dir,
                 cell_px, cell_m):
    """Pool and write the cell store. A failure here must not lose the tile itself — the change
    map is the primary product and it is already computed by this point."""
    from . import cells as CE
    try:
        path = os.path.join(out_dir, CE.cells_filename(tile, year_a, year_b, cell_m))
        if os.path.exists(path):
            return
        ma, mb, n, smean, smax = CE.pool(a, b, sc, cov == S.COV_OK, cell_px)
        CE.write_cells(path, ma, mb, n, smean, smax, crs, transform, cell_px)
    except Exception:
        pass


def process_tile(src, tile, year_a, year_b, out_grid, out_dir, cell_px=None,
                 cell_m=CE_CELL_M):
    """Fetch, score, reproject, write. Returns the tile record for the VRT, or None if the
    tile falls outside the output grid or neither year could be fetched.

    `cell_px` also pools the embeddings into a cell store beside the tile — see cells.py. It
    has to happen here because this is the only place both years are in memory.
    """
    from .gdalio import array_bounds

    path = os.path.join(out_dir, tile_filename(tile, year_a, year_b, out_grid))
    if os.path.exists(path) and _cells_done(out_dir, tile, year_a, year_b, cell_px, cell_m):
        rec = _record_for_existing(path, out_grid)   # resumable: already done
        if rec is not None:
            return rec                             # else it's stale — fall through and rebuild

    a, crs_a, tr_a = _try_fetch(src, tile, year_a)
    b, crs_b, tr_b = _try_fetch(src, tile, year_b)
    if a is None and b is None:
        return None                                # nothing at all here; VRT leaves it no-tile
    crs = crs_a if crs_a is not None else crs_b
    transform = tr_a if tr_a is not None else tr_b

    # Place the tile by its ACTUAL fetched bounds, never by its identifier. This cost a whole
    # debugging cycle against an earlier data source, whose tiles were named by their CENTRE:
    # name-based placement offset every tile by half a tile and left a grid of gaps. The rule
    # stands regardless of source — a fetch is allowed to return fewer rows/cols than asked
    # for at a COG's edge.
    ref = a if a is not None else b
    h0, w0 = ref.shape[:2]
    win = G.window_for_bounds(out_grid, array_bounds(h0, w0, transform), crs)
    if win is None:
        return None
    r0, r1, c0, c1 = win
    rec = {"path": path, "row0": r0, "col0": c0, "width": c1 - c0, "height": r1 - r0}

    if a is None or b is None or a.shape != b.shape:
        # one year missing (or unusable): emit a nodata tile that RECORDS WHY, rather than a
        # hole that looks the same as "nothing changed"
        ref = a if a is not None else b
        shape2 = ref.shape[:2]
        sc = np.full(shape2, S.NODATA, dtype=np.float32)
        cov = S.coverage(S.valid_mask(a) if a is not None and a.shape == ref.shape else None,
                         S.valid_mask(b) if b is not None and b.shape == ref.shape else None,
                         shape=shape2)
    else:
        sc, cov = S.change_score(a, b)
        if cell_px:
            # LAST CHANCE: after this the embeddings are gone and only a re-download brings them
            # back. No threshold is applied — see cells.py for why that has to wait.
            _write_cells(a, b, sc, cov, crs, transform, tile, year_a, year_b, out_dir,
                         cell_px, cell_m)
    del a, b                                        # free the 2x64 bands immediately

    dst_transform = G.transform_of(out_grid, r0, c0)
    shape = (rec["height"], rec["width"])
    sc_out = _reproject_into(sc, crs, transform, out_grid.crs, dst_transform, shape,
                             S.NODATA, nearest=False)
    # parts of this tile's window the source doesn't reach are "no tile", not "missing both"
    cov_out = _reproject_into(cov.astype(np.float32), crs, transform, out_grid.crs,
                              dst_transform, shape, float(S.COV_NO_TILE), nearest=True)
    # The two bands are resampled DIFFERENTLY (average for the score so downsampling doesn't
    # alias, nearest for the coverage so its class codes stay codes), and the two disagree by
    # up to a pixel at the tile's warped edge: `average` keeps an output pixel if ANY source
    # pixel touches it, `nearest` only if the pixel centre lands inside. That left a one-pixel
    # rim that carried a score while the coverage band called it "not covered" — an outline
    # traced around every single tile. Coverage is the authority on what this tile reached, so
    # the score defers to it. Neighbouring windows overlap by far more than a pixel, so nothing
    # is lost except a rim at the outer edge of the whole mosaic.
    sc_out[cov_out == S.COV_NO_TILE] = S.NODATA

    from . import gdalio as GD
    tmp = path + ".part"
    GD.write(tmp, np.stack([sc_out, cov_out]), out_grid.crs, dst_transform, nodata=S.NODATA,
             options={"compress": "deflate", "tiled": "YES"})
    os.replace(tmp, path)      # only complete files ever appear -> safe to read while running
    rec["hist"] = S.histogram(sc_out)
    return rec


def run(bbox, year_a, year_b, out_dir, dst_crs="EPSG:3857", res_m=10.0,
        cache_dir=None, on_tile=None, should_stop=None, src=None, cell_m=None):
    """Run the whole job, yielding progress. `on_tile(done, total, rec, hist_total)` is called
    as each tile lands so the caller can refresh a map. Returns (grid, tiles, hist_total)."""
    src = src or open_source(cache_dir, res_m)
    # Cells are a fixed size on the GROUND (160 m), so how many source pixels that is depends on
    # which overview we are reading. Deriving it here is what keeps the cell store valid across
    # Detail changes instead of silently re-fetching.
    cell_px = None if not cell_m else max(1, int(round(cell_m / src.res)))
    all_tiles, both, partial = list_tiles(src, bbox, year_a, year_b)
    out_grid = G.make_grid(bbox, dst_crs, res_m)
    os.makedirs(out_dir, exist_ok=True)

    # ponytail: tiles run one at a time. An earlier source needed a prefetch thread because
    # a whole 150 MB tile had to land on disk before it was usable; a COG window read is
    # already parallel INSIDE GDAL (GDAL_HTTP_MULTIRANGE issues concurrent range requests), so
    # a thread pool here would mostly contend for the same bandwidth. If tile throughput ever
    # matters, the upgrade is a bounded queue of fetched pairs — but note each pair is ~0.5 GB
    # of float32, so that costs memory per slot.
    records, hist_total, done = [], np.zeros(S.HIST_BINS, dtype=np.int64), 0
    for tile in all_tiles:
        if should_stop is not None and should_stop():
            break
        try:
            rec = process_tile(src, tile, year_a, year_b, out_grid, out_dir,
                               cell_px, cell_m)
        except Exception:
            rec = None                               # a bad tile must not kill the job
        done += 1
        if rec is not None:
            hist_total += rec.pop("hist")
            records.append(rec)
            if on_tile is not None:
                on_tile(done, len(all_tiles), rec, hist_total)
    return out_grid, records, hist_total, partial
