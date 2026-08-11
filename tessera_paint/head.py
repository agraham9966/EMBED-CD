"""Portable multi-class head — classes COMPETE (one label per pixel), and the model is
scene-independent so it can be TRAINED on one area and APPLIED to another (or saved/loaded).

Two design choices make it portable:
  1. It trains on RAW TESSERA embeddings (the same global space everywhere), NOT the
     per-scene standardized cube. A head fitted in one scene's local z-scores can't be applied
     to another; raw embeddings are comparable across scenes (same frozen encoder).
  2. It carries its OWN standardization (mean/std learned from the training pixels) and applies
     that identically to any target area. So predict() is a fixed function of the head, not of
     the target scene.

Model: shrinkage LDA (regularized Gaussian classifier, pooled within-class covariance). Closed
form — no optimizer — sample-efficient enough for a few brush strokes, and it learns which
dimensions discriminate rather than weighting all 128 equally. Degrades to nearest-mean when a
class has a single sample.
"""
import numpy as np

_EPS = 1e-8


def train(class_vectors, shrinkage=0.2):
    """class_vectors: list of [n_k, d] float arrays of RAW embeddings, one per class in label
    order. Returns a portable head dict {W, b, mean, std, K, d}."""
    if len(class_vectors) < 1:
        raise ValueError("need at least one class")
    d = int(np.asarray(class_vectors[0]).shape[1])
    parts = [np.asarray(v, np.float64).reshape(-1, d) for v in class_vectors]

    allX = np.concatenate(parts, axis=0)
    mean = allX.mean(axis=0)
    std = allX.std(axis=0)                                  # the head's own standardization
    med = float(np.median(std[std > _EPS])) if np.any(std > _EPS) else 1.0
    std = np.maximum(std, 0.3 * med)                        # floor: no dim explodes on few samples
    parts = [(p - mean) / std for p in parts]

    K = len(parts)
    means = np.stack([p.mean(axis=0) for p in parts])       # [K,d]
    scatter = np.zeros((d, d), dtype=np.float64)
    n = 0
    for p, m in zip(parts, means):
        Xc = p - m
        scatter += Xc.T @ Xc
        n += p.shape[0]
    cov = scatter / max(n - K, 1)
    scale = float(np.trace(cov)) / d
    if scale <= _EPS:                                        # 1 sample/class -> nearest-mean
        cov = np.eye(d)
    else:
        cov = (1.0 - shrinkage) * cov + shrinkage * scale * np.eye(d)
    cov_inv = np.linalg.pinv(cov)

    W = cov_inv @ means.T                                   # [d,K]
    b = -0.5 * np.sum((means @ cov_inv) * means, axis=1)    # [K]
    return {"W": W.astype(np.float32), "b": b.astype(np.float32),
            "mean": mean.astype(np.float32), "std": std.astype(np.float32),
            "K": K, "d": d}


def predict(cube, valid, head, chunk_rows=256):
    """cube: [H,W,d] RAW embeddings (the target area). Returns (labels int16 [H,W], margin
    float32 [H,W] in [0,1]). labels: class index, or -1 for nodata. margin: how decisively the
    winner beat the runner-up (percentile-normalized) — drive abstention off this."""
    W, b, mean, std = head["W"], head["b"], head["mean"], head["std"]
    h, w, d = cube.shape
    K = W.shape[1]
    lab = np.empty(h * w, dtype=np.int16)
    mar = np.empty(h * w, dtype=np.float32)

    for r0 in range(0, h, chunk_rows):
        r1 = min(r0 + chunk_rows, h)
        zf = (np.asarray(cube[r0:r1], np.float32).reshape(-1, d) - mean) / std
        L = zf @ W + b
        top = np.argmax(L, axis=1)
        rows = np.arange(L.shape[0])
        best = L[rows, top]
        if K > 1:
            L[rows, top] = -np.inf
            second = L.max(axis=1)
        else:
            second = np.zeros_like(best)
        s0, s1 = r0 * w, r1 * w
        lab[s0:s1] = top.astype(np.int16)
        mar[s0:s1] = (best - second).astype(np.float32)

    vflat = valid.reshape(-1)
    vals = mar[vflat]
    vals = vals[np.isfinite(vals)]
    if vals.size:
        step = max(1, vals.size // 100000)
        lo, hi = np.percentile(vals[::step], [2.0, 98.0])
        mar = np.clip((mar - lo) / max(float(hi - lo), _EPS), 0.0, 1.0)
    mar[~np.isfinite(mar)] = 0.0
    lab[~vflat] = -1
    mar[~vflat] = 0.0
    return lab.reshape(h, w), mar.reshape(h, w)


def apply_confidence(labels, margin, min_confidence):
    """[H,W] int16 with pixels below the confidence bar set to -1. Cheap re-thresholding —
    no need to re-run predict when the slider moves."""
    out = labels.copy()
    out[margin < min_confidence] = -1
    return out


def save(head, path):
    """Persist the model weights (np .npz). Class names/colors are the caller's concern."""
    np.savez(path, W=head["W"], b=head["b"], mean=head["mean"], std=head["std"],
             K=np.int64(head["K"]), d=np.int64(head["d"]))


def load(path):
    with np.load(path) as z:
        return {"W": z["W"], "b": z["b"], "mean": z["mean"], "std": z["std"],
                "K": int(z["K"]), "d": int(z["d"])}
