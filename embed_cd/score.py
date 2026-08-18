"""Change scoring for a single tile pair. Pure numpy — no I/O, no QGIS.

Design rules that matter for a TILED mosaic:

* The score is ABSOLUTE (cosine distance, 0..1), never percentile-stretched. Per-tile
  normalization would give every tile its own scale and produce visible seams at tile
  boundaries — and a threshold that means something different in each tile.
* Missing data is a CODE, never 0. A gap scored 0 is indistinguishable from "nothing
  changed", which is worse than no answer.
* The histogram accumulates across tiles so an automatic (Otsu) cutoff can be chosen for the
  whole mosaic without ever holding the whole mosaic in memory.
"""
import numpy as np

_EPS = 1e-8

NODATA = -1.0            # written to the score band where a year is missing

# Coverage codes. 0 MUST mean "no tile here": a VRT fills areas no source covers with 0, so if
# 0 meant "fine" the map would claim we had data where we never even looked.
COV_NO_TILE, COV_OK, COV_MISSING_A, COV_MISSING_B, COV_MISSING_BOTH = 0, 1, 2, 3, 4
COV_LABELS = {COV_NO_TILE: "not covered (no tile)",
              COV_OK: "data in both years",
              COV_MISSING_A: "no data in year A",
              COV_MISSING_B: "no data in year B",
              COV_MISSING_BOTH: "no data in either year"}

HIST_BINS = 256          # score is bounded 0..1, so fixed bins merge trivially across tiles


def valid_mask(cube):
    """[H,W] bool: pixels with a usable embedding (finite, non-zero)."""
    if cube is None:
        return None
    n = np.linalg.norm(np.asarray(cube, dtype=np.float32), axis=2)
    return np.isfinite(n) & (n > 0)


def coverage(valid_a, valid_b, shape=None):
    """[H,W] uint8 saying WHY a pixel is (un)usable. A missing year is passed as None.
    Never returns COV_NO_TILE — that value exists only for grid cells no tile reached."""
    if valid_a is None and valid_b is None:
        return np.full(shape, COV_MISSING_BOTH, dtype=np.uint8)
    if valid_a is None:
        return np.where(valid_b, COV_MISSING_A, COV_MISSING_BOTH).astype(np.uint8)
    if valid_b is None:
        return np.where(valid_a, COV_MISSING_B, COV_MISSING_BOTH).astype(np.uint8)
    out = np.full(valid_a.shape, COV_OK, dtype=np.uint8)
    out[~valid_a & valid_b] = COV_MISSING_A
    out[valid_a & ~valid_b] = COV_MISSING_B
    out[~valid_a & ~valid_b] = COV_MISSING_BOTH
    return out


def change_score(raw_a, raw_b, chunk_rows=256):
    """(score [H,W] float32 in 0..1 with NODATA where unusable, coverage [H,W] uint8).

    Both cubes come from the SAME source tile, so they already share a grid — no alignment
    or resampling is needed or done here.
    """
    if raw_a.shape != raw_b.shape:
        raise ValueError(f"tile years differ in shape: {raw_a.shape} vs {raw_b.shape}")
    va, vb = valid_mask(raw_a), valid_mask(raw_b)
    cov = coverage(va, vb)
    usable = va & vb

    h, w, _ = raw_a.shape
    out = np.full((h, w), NODATA, dtype=np.float32)
    for r0 in range(0, h, chunk_rows):
        r1 = min(r0 + chunk_rows, h)
        m = usable[r0:r1]
        if not m.any():
            continue
        a = np.asarray(raw_a[r0:r1], dtype=np.float32)
        b = np.asarray(raw_b[r0:r1], dtype=np.float32)
        na = np.linalg.norm(a, axis=2, keepdims=True)
        nb = np.linalg.norm(b, axis=2, keepdims=True)
        cos = np.sum((a / np.maximum(na, _EPS)) * (b / np.maximum(nb, _EPS)), axis=2)
        block = np.clip((1.0 - cos) * 0.5, 0.0, 1.0)      # cosine distance 0..2 -> 0..1
        out[r0:r1] = np.where(m, block, NODATA)
    return out, cov


def histogram(score):
    """[HIST_BINS] int64 counts of valid scores, over a FIXED 0..1 range so per-tile
    histograms can simply be summed."""
    vals = score[score != NODATA]
    vals = vals[np.isfinite(vals)]
    hist, _ = np.histogram(vals, bins=HIST_BINS, range=(0.0, 1.0))
    return hist.astype(np.int64)


def otsu_from_histogram(hist):
    """Otsu's cutoff from accumulated counts: the value best splitting the mosaic into
    unchanged/changed by maximizing between-group variance. Returns a score in 0..1."""
    hist = np.asarray(hist, dtype=np.float64)
    total = hist.sum()
    if total <= 0:
        return 0.5
    centers = (np.arange(HIST_BINS) + 0.5) / HIST_BINS
    w0 = np.cumsum(hist)
    w1 = total - w0
    csum = np.cumsum(hist * centers)
    with np.errstate(invalid="ignore", divide="ignore"):
        m0 = csum / w0
        m1 = (csum[-1] - csum) / w1
        between = w0 * w1 * (m0 - m1) ** 2
    between[~np.isfinite(between)] = -np.inf
    if not np.isfinite(between).any() or between.max() <= 0:
        return float(np.percentile(np.repeat(centers, hist.astype(int)), 95)) if total else 0.5
    return float(centers[int(np.argmax(between))])


def fraction_above(hist, threshold):
    """Share of valid pixels at or above a cutoff — for the '4.1% of the area changed' line."""
    hist = np.asarray(hist, dtype=np.float64)
    total = hist.sum()
    if total <= 0:
        return 0.0
    idx = int(np.clip(round(threshold * HIST_BINS), 0, HIST_BINS))
    return float(hist[idx:].sum() / total)
