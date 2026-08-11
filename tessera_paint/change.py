"""Year-over-year change from two pixel-aligned embedding cubes. No I/O, no QGIS.

Two things live here:

1. `change_score(a, b)` — unsupervised "how much did this pixel change": cosine distance
   between the two years' raw embeddings, scaled to [0,1]. Deliberately ABSOLUTE (not
   percentile-stretched like the similarity heatmap) so a threshold means the same thing
   across year-pairs and areas — which is the whole point when comparing change.

2. `ChangeFeatures(a, b)` — a LAZY [H,W,2d] view giving `[baseline, delta]` per pixel, for
   classifying *what kind* of change happened. Baseline+delta is the representation that
   works: delta alone can't tell "forest->bare" from "bare->forest", and a single similarity
   score collapses the space entirely. Lazy because materializing 256-d would double memory;
   it exposes just enough (`.shape`, slicing) for head.train/head.predict to consume it.
"""
import numpy as np

_EPS = 1e-8


def regrid(arr, src_transform, dst_transform, crs, dst_hw, nodata=np.nan):
    """Resample `arr` ([H,W] or [H,W,C]) onto the dst grid (same CRS, different transform/shape),
    nearest-neighbour so distinct class signatures are never blended. Areas the source doesn't
    cover become `nodata`."""
    from rasterio.warp import reproject, Resampling
    h, w = dst_hw
    src = arr[None] if arr.ndim == 2 else np.moveaxis(arr, 2, 0)
    dst = np.full((src.shape[0], h, w), nodata, dtype=np.float32)
    reproject(np.ascontiguousarray(src, dtype=np.float32), dst,
              src_transform=src_transform, src_crs=crs,
              dst_transform=dst_transform, dst_crs=crs,
              src_nodata=nodata, dst_nodata=nodata, resampling=Resampling.nearest)
    return dst[0] if arr.ndim == 2 else np.moveaxis(dst, 0, 2)


def align(a, ta, b, tb, crs, tol=1e-4):
    """Put year B on year A's exact pixel grid, so the two cubes line up for differencing.

    Necessary because geotessera derives the output resolution per fetch from that year's
    available tiles — so the same region/Detail can come back with sub-pixel-different pixel
    sizes or offsets between years. If they already match, it's a no-op; otherwise B is
    resampled (nearest) onto A's grid. Returns (a, b_on_a_grid, a_transform).
    """
    same = (a.shape[:2] == b.shape[:2]
            and abs(ta.a - tb.a) < tol and abs(ta.e - tb.e) < tol
            and abs(ta.c - tb.c) < abs(ta.a) * 0.1 and abs(ta.f - tb.f) < abs(ta.e) * 0.1)
    if same:
        return a, b, ta
    return a, regrid(b, tb, ta, crs, a.shape[:2]), ta


def _unit(cube_block):
    """L2-normalize each pixel vector of a [...,d] block (raw embeddings)."""
    b = np.asarray(cube_block, dtype=np.float32)
    n = np.linalg.norm(b, axis=-1, keepdims=True)
    return b / np.maximum(n, _EPS)


def change_score(raw_a, raw_b, valid=None, chunk_rows=256):
    """[H,W] float32 in [0,1]: cosine distance between year A and year B embeddings.

    0 = identical signature, higher = more changed. Absolute scale (no percentile stretch),
    so thresholds are comparable between runs. Invalid/nodata pixels -> 0.
    """
    if raw_a.shape != raw_b.shape:
        raise ValueError(f"years are not pixel-aligned: {raw_a.shape} vs {raw_b.shape}")
    h, w, _ = raw_a.shape
    out = np.empty((h, w), dtype=np.float32)
    for r0 in range(0, h, chunk_rows):
        r1 = min(r0 + chunk_rows, h)
        za = _unit(raw_a[r0:r1])
        zb = _unit(raw_b[r0:r1])
        cos = np.sum(za * zb, axis=-1)
        out[r0:r1] = (1.0 - cos) * 0.5      # cosine distance 0..2 -> 0..1
    np.clip(out, 0.0, 1.0, out=out)
    out[~np.isfinite(out)] = 0.0
    if valid is not None:
        out[~valid] = 0.0
    return out


#: coverage codes — nodata is a CATEGORY, not a silent hole. Scoring it 0 would make
#: "we couldn't look" indistinguishable from "nothing changed", which is worse than useless.
COV_OK, COV_MISSING_A, COV_MISSING_B, COV_MISSING_BOTH = 0, 1, 2, 3
COV_LABELS = {COV_MISSING_A: "no data in A", COV_MISSING_B: "no data in B",
              COV_MISSING_BOTH: "no data in both"}


def coverage(valid_a, valid_b):
    """[H,W] uint8 saying WHY a pixel is (un)usable: 0 both years present, 1 missing in A,
    2 missing in B, 3 missing in both."""
    a = np.asarray(valid_a, bool)
    b = np.asarray(valid_b, bool)
    out = np.zeros(a.shape, dtype=np.uint8)
    out[~a & b] = COV_MISSING_A
    out[a & ~b] = COV_MISSING_B
    out[~a & ~b] = COV_MISSING_BOTH
    return out


def categorize(score, coverage_map, threshold):
    """[H,W] uint8 combining change and coverage into one labelled map:
    0 = unchanged (background), 1 = changed, 2 = no data in one year, 3 = no data in both.
    Lets the change layer show missing data instead of pretending it was 'no change'."""
    out = np.where(np.asarray(score) >= threshold, 1, 0).astype(np.uint8)
    cov = np.asarray(coverage_map)
    out[(cov == COV_MISSING_A) | (cov == COV_MISSING_B)] = 2
    out[cov == COV_MISSING_BOTH] = 3
    return out


CATEGORY_LABELS = {1: "changed", 2: "no data (one year)", 3: "no data (both years)"}


def suggest_threshold(score, valid=None, percentile=95.0):
    """A sensible starting cutoff: the given percentile of valid change scores. Real change is
    rare, so an absolute scale would otherwise leave the slider showing nothing at 0.5."""
    vals = score[valid] if valid is not None else score.reshape(-1)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return 0.5
    step = max(1, vals.size // 100000)
    return float(np.percentile(vals[::step], percentile))


def otsu_threshold(score, valid=None, bins=256):
    """Automatic cutoff by Otsu's method: the value that best splits the change scores into
    two groups (unchanged / changed) by maximizing between-group variance.

    This is the standard way to threshold a change-magnitude image, and it beats guessing a
    percentile because it adapts to the actual distribution — a quiet scene and a heavily
    disturbed one get different, appropriate cutoffs. Falls back to the p95 heuristic when the
    histogram has no meaningful split (e.g. nothing changed at all).
    """
    vals = score[valid] if valid is not None else score.reshape(-1)
    vals = vals[np.isfinite(vals)]
    if vals.size < 16:
        return suggest_threshold(score, valid)
    lo, hi = float(vals.min()), float(vals.max())
    if hi - lo < 1e-6:
        return suggest_threshold(score, valid)

    hist, edges = np.histogram(vals, bins=bins, range=(lo, hi))
    hist = hist.astype(np.float64)
    w0 = np.cumsum(hist)                       # weight of the "unchanged" group
    w1 = w0[-1] - w0
    centers = (edges[:-1] + edges[1:]) / 2.0
    csum = np.cumsum(hist * centers)
    with np.errstate(invalid="ignore", divide="ignore"):
        m0 = csum / w0
        m1 = (csum[-1] - csum) / w1
        between = w0 * w1 * (m0 - m1) ** 2     # maximize this
    between[~np.isfinite(between)] = -np.inf
    if not np.isfinite(between).any() or between.max() <= 0:
        return suggest_threshold(score, valid)
    return float(centers[int(np.argmax(between))])


class ChangeFeatures:
    """Lazy [H,W,2d] cube of `[baseline_a, b - a]` built on demand from two raw cubes.

    Supports the slicing head.train/head.predict need (`feat[r0:r1]`, `feat[rows, cols]`)
    without ever materializing the doubled array.
    """

    def __init__(self, raw_a, raw_b):
        if raw_a.shape != raw_b.shape:
            raise ValueError(f"years are not pixel-aligned: {raw_a.shape} vs {raw_b.shape}")
        self.a = raw_a
        self.b = raw_b
        h, w, d = raw_a.shape
        self.shape = (h, w, 2 * d)
        self.dtype = np.float32

    def __getitem__(self, key):
        a = np.asarray(self.a[key], dtype=np.float32)
        b = np.asarray(self.b[key], dtype=np.float32)
        return np.concatenate([a, b - a], axis=-1)

    def __array__(self, dtype=None):     # only if something really wants the whole thing
        out = self[:]
        return out if dtype is None else out.astype(dtype)
