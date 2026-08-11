"""Similarity and masking over a float32 embedding cube. No I/O, no QGIS.

How we compare (this matters — a single raw cosine collapses the 128-d space and loses
class separability):
  1. STANDARDIZE per channel (z-score over the mosaic) so no high-variance dim dominates.
  2. cosine of each pixel to the mean *positive* seed, in standardized space.
  3. NEGATIVE seeds apply a bounded margin penalty: only pixels that are closer to the
     exclude prototype than to the include one get pushed down. Solid matches are untouched,
     so one right-click refines instead of deleting the class.
  4. normalize the result to [0,1] so the threshold means "keep the top fraction" and stays
     stable whether or not negatives are present.

Computed in row chunks so we never hold a second full-size copy.
"""
import numpy as np

_EPS = 1e-8


def _has(vectors):
    """True if a seed collection is non-empty. Callers pass lists OR ndarrays, and
    `if ndarray:` raises — so never test these for truthiness directly."""
    return vectors is not None and len(vectors) > 0


def mosaic_stats(embeddings, sample=200000):
    """One pass over the cube -> (mean[128], std[128], nodata_fraction).

    Fuses the standardization stats and the nodata report (they both used to scan the whole
    cube). Validity from the per-pixel norm (one pass, small output — cheaper than isfinite.all
    over 128 dims). mean/std from a random sample of valid pixels: statistically identical to
    the full set, near-zero extra memory, and fast on huge mosaics."""
    h, w, c = embeddings.shape
    flat = np.asarray(embeddings, dtype=np.float32).reshape(-1, c)
    norm = np.linalg.norm(flat, axis=1)                 # single full read, [-> N] output
    valid = np.isfinite(norm) & (norm > 0)
    nodata = 1.0 - float(valid.mean())
    idx = np.flatnonzero(valid)
    if idx.size == 0:
        return np.zeros(c, np.float32), np.ones(c, np.float32), nodata
    if idx.size > sample:
        idx = np.random.default_rng(0).choice(idx, sample, replace=False)
    X = flat[idx]                                        # copies only the sampled rows
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < _EPS] = 1.0    # constant channels contribute nothing after centering
    return mean.astype(np.float32), std.astype(np.float32), nodata


def standardize_stats(embeddings):
    """(mean[128], std[128]) — thin wrapper over mosaic_stats for callers that don't need
    the nodata fraction (e.g. similarity() computing its own stats, and the tests)."""
    mean, std, _ = mosaic_stats(embeddings)
    return mean, std


def _prototype(vectors, mean, std):
    z = (np.asarray(vectors, dtype=np.float32) - mean) / std
    z = z / np.maximum(np.linalg.norm(z, axis=1, keepdims=True), _EPS)  # unit each seed
    proto = z.mean(axis=0)
    n = float(np.linalg.norm(proto))
    if n == 0:
        raise ValueError("seed vector(s) have zero norm; click a non-empty pixel")
    return proto / n


def similarity(embeddings, seed_vectors, neg_vectors=None, stats=None,
               neg_weight=1.0, chunk_rows=64):
    """[H,W] float32 in [0,1]: how strongly each pixel matches the include seeds, after
    excluding look-alikes of the negative seeds. Nodata/zero-norm pixels -> 0."""
    if stats is None:
        stats = standardize_stats(embeddings)
    mean, std = stats
    pos = _prototype(seed_vectors, mean, std)
    neg = _prototype(neg_vectors, mean, std) if _has(neg_vectors) else None

    h, w, c = embeddings.shape
    out = np.empty((h, w), dtype=np.float32)
    for r0 in range(0, h, chunk_rows):
        r1 = min(r0 + chunk_rows, h)
        bf = np.asarray(embeddings[r0:r1], dtype=np.float32).reshape(-1, c)
        bad = ~(np.isfinite(bf).all(axis=1) & (np.linalg.norm(bf, axis=1) > 0))
        z = (bf - mean) / std
        z = z / np.maximum(np.linalg.norm(z, axis=1, keepdims=True), _EPS)
        sp = z @ pos
        if neg is not None:
            sn = z @ neg
            sp = sp - neg_weight * np.maximum(sn - sp, 0.0)  # penalize only look-alikes
        sp[bad] = np.nan   # nodata / empty pixels drop out of normalization -> 0
        out[r0:r1] = sp.reshape(r1 - r0, w)

    finite = np.isfinite(out)
    if finite.any():
        lo, hi = np.percentile(out[finite], [2.0, 98.0])
        out = np.clip((out - lo) / max(float(hi - lo), _EPS), 0.0, 1.0)
    out[~finite] = 0.0
    return out.astype(np.float32)


def mask(simmap, threshold):
    """[H, W] uint8: 1 where similarity >= threshold, else 0."""
    return (np.asarray(simmap) >= threshold).astype(np.uint8)


# ---- fast path: standardize once at load, then every click is a single matmul ----

def prepare(embeddings, sample=200000, chunk_rows=256):
    """Standardize (per channel) + L2-normalize every pixel into a NEW cube. Returns
    (znorm float32 [H,W,128], valid bool [H,W], nodata_fraction). Leaves `embeddings` intact
    (the dock keeps the raw cube for the portable classifier head).

    After this, similarity for any seed is just `znorm @ prototype` — no per-click
    standardization, which is what made clicks slow on big regions."""
    h, w, c = embeddings.shape
    src = embeddings.reshape(-1, c)
    norm = np.linalg.norm(src, axis=1)                  # one read
    valid = np.isfinite(norm) & (norm > 0)
    nodata = 1.0 - float(valid.mean())

    znorm = np.zeros((h * w, c), dtype=np.float32)
    idx = np.flatnonzero(valid)
    if idx.size == 0:
        return znorm.reshape(h, w, c), valid.reshape(h, w), nodata
    s = idx if idx.size <= sample else np.random.default_rng(0).choice(idx, sample, replace=False)
    X = np.asarray(src[s], np.float32)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < _EPS] = 1.0

    for r0 in range(0, src.shape[0], chunk_rows * w):   # standardize+normalize into znorm
        r1 = min(r0 + chunk_rows * w, src.shape[0])
        b = (np.asarray(src[r0:r1], np.float32) - mean) / std
        n = np.linalg.norm(b, axis=1, keepdims=True)
        znorm[r0:r1] = b / np.maximum(n, _EPS)
    znorm[~valid] = 0.0                                 # nodata pixels -> zero vector
    return znorm.reshape(h, w, c), valid.reshape(h, w), nodata


def _unit(vectors):
    v = np.mean(np.asarray(vectors, dtype=np.float32), axis=0)
    n = float(np.linalg.norm(v))
    if n == 0:
        raise ValueError("seed vector(s) have zero norm; click a non-empty pixel")
    return v / n


def score(znorm, valid, seed_vectors, neg_vectors=None, neg_weight=1.0):
    """[H,W] float32 in [0,1] from a prepared (standardized+normalized) cube. Seeds are
    prepared pixel vectors. Bounded negative margin; nodata (from `valid`) -> 0.

    One BLAS matmul over the whole cube (znorm rows are views, no copies) + a strided
    sample for the [0,1] percentile stretch — fast enough to feel instant per click."""
    c = znorm.shape[2]
    pos = _unit(seed_vectors)
    zf = znorm.reshape(-1, c)                       # view
    sp = zf @ pos                                   # [N]
    if _has(neg_vectors):
        neg = _unit(neg_vectors)
        sp = sp - neg_weight * np.maximum(zf @ neg - sp, 0.0)

    vflat = valid.reshape(-1)
    vals = sp[vflat]
    if vals.size:
        step = max(1, vals.size // 100000)          # ~100k-sample percentile (plenty)
        lo, hi = np.percentile(vals[::step], [2.0, 98.0])
        sp = np.clip((sp - lo) / max(float(hi - lo), _EPS), 0.0, 1.0)
    out = np.where(vflat, sp, 0.0).astype(np.float32)
    return out.reshape(valid.shape)
