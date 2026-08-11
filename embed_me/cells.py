"""Keep a coarse copy of the embeddings, so the change map can be asked what happened.

A tile's embeddings exist for about six seconds — long enough to score them, and then they are
freed so memory stays flat. That is the only window in which anything can be attached to them.
But the threshold that decides which polygons are worth describing cannot be known until the
whole job has finished, because the same score means different things in different landscapes
(0.03 is the top 0.12% of a quiet scene and the top 19% of a working forest).

So this module refuses to make any decision. It pools every pixel into fixed cells, with no
threshold anywhere, and writes the result beside the tile. Afterwards, any polygon cut at any
threshold gets its vector as the count-weighted mean of the cells it covers. Nothing written here
depends on a cutoff, which is what makes the cutoff free to change later.

Pooling every cell (rather than only cells "near change") is deliberate: deciding what is near
change needs a threshold, which is the very thing we cannot know yet. It also makes the store a
function of area alone — predictable, instead of exploding in exactly the busy scenes that matter.

See docs/superpowers/specs/2026-08-04-tunable-change-classifier-design.md
"""
import os

import numpy as np

CELL_PX = 16              # 16 x 10 m = 160 m cells. 1024 / 16 = 64, so cells never cross tiles.
N_BAND, SMEAN_BAND, SMAX_BAND = -3, -2, -1


def cells_filename(tile, year_a, year_b, cell_px=CELL_PX):
    """Every input that changes the CONTENT of the file appears in its name — the same rule the
    tile files learned the hard way in 0.5.2, where a re-run at a different Detail silently reused
    tiles built for another grid.

    The output CRS and resolution are deliberately absent: pooling happens on the fetched cube in
    its native UTM grid before anything is reprojected, so changing Detail does not invalidate
    these files.
    """
    return (f"cells_{year_a}-{year_b}_{cell_px}px_"
            f"{tile.crs.replace(':', '')}_{int(tile.west)}_{int(tile.south)}.tif")


def pool(cube_a, cube_b, score, usable, cell_px=CELL_PX):
    """Reduce a tile to a grid of cells: (mean_a, mean_b, n, score_mean, score_max).

    Means, not sums. Cells never straddle tiles (a tile is a block-aligned 1024 px window and
    1024/16 = 64 exactly), so they never need merging; `n` carries the weight for the one place
    that does need it — a polygon spanning several tiles.

    Runs one cell-row at a time. The transient is a few MB, not another copy of the 268 MB cube,
    which matters because this runs while BOTH years are still resident.
    """
    h, w = usable.shape
    ch, cw = -(-h // cell_px), -(-w // cell_px)
    depth = cube_a.shape[2]
    ma = np.zeros((ch, cw, depth), np.float32)
    mb = np.zeros((ch, cw, depth), np.float32)
    n = np.zeros((ch, cw), np.float32)
    smean = np.zeros((ch, cw), np.float32)
    smax = np.zeros((ch, cw), np.float32)
    pad_w = cw * cell_px - w

    for ci in range(ch):
        r0 = ci * cell_px
        r1 = min(r0 + cell_px, h)
        pad_r = cell_px - (r1 - r0)
        pad = ((0, pad_r), (0, pad_w))

        u = usable[r0:r1]
        if pad_r or pad_w:
            u = np.pad(u, pad)
        counts = u.reshape(cell_px, cw, cell_px).sum(axis=(0, 2)).astype(np.float32)
        n[ci] = counts
        safe = np.maximum(counts, 1.0)[:, None]

        for out, cube in ((ma, cube_a), (mb, cube_b)):
            blk = cube[r0:r1]
            if pad_r or pad_w:
                blk = np.pad(blk, pad + ((0, 0),))
            blk = blk * u[:, :, None]           # unusable pixels contribute nothing
            out[ci] = blk.reshape(cell_px, cw, cell_px, depth).sum(axis=(0, 2)) / safe

        s = score[r0:r1]
        if pad_r or pad_w:
            s = np.pad(s, pad)
        s = np.where(u, s, 0.0)
        smean[ci] = s.reshape(cell_px, cw, cell_px).sum(axis=(0, 2)) / safe[:, 0]
        smax[ci] = s.reshape(cell_px, cw, cell_px).max(axis=(0, 2))

    return ma, mb, n, smean, smax


def write_cells(path, ma, mb, n, smean, smax, crs, transform, cell_px=CELL_PX):
    """One self-describing GeoTIFF: 2*depth embedding bands then n, score mean, score max.

    Untiled on purpose. GeoTIFF tiles are 256x256 minimum but a cell grid is 64x64, so tiling pads
    every band — measured 34.3 MB of padding around 2.15 MB of real data.
    """
    from . import gdalio as G

    depth = ma.shape[2]
    stack = np.concatenate(
        [ma, mb, n[:, :, None], smean[:, :, None], smax[:, :, None]], axis=2)
    cell_tr = G.Transform(transform.a * cell_px, 0.0, transform.c,
                          0.0, transform.e * cell_px, transform.f)
    tmp = path + ".part"
    G.write(tmp, np.moveaxis(stack, -1, 0), crs, cell_tr, options={"compress": "deflate"})
    os.replace(tmp, path)       # only complete files ever appear
    return path


def read_cells(path):
    """(mean_a, mean_b, n, score_mean, score_max, crs, transform) — the inverse of write_cells."""
    from . import gdalio as G
    arr, crs, transform = G.read(path)                     # arr is [bands, H, W]
    depth = (arr.shape[0] - 3) // 2
    move = lambda a: np.moveaxis(a, 0, -1)                 # noqa: E731
    return (move(arr[:depth]), move(arr[depth:2 * depth]),
            arr[N_BAND], arr[SMEAN_BAND], arr[SMAX_BAND], crs, transform)
