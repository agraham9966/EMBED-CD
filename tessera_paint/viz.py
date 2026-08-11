"""Visualize the embedding cube itself: project 128-d -> 3-d (PCA) and stretch to RGB.

This is the honest "what am I clicking" view — pixels with similar year-long multi-sensor
signatures get similar colors, straight from the data the similarity runs on. No I/O, no QGIS.
"""
import numpy as np


def pca_rgb(embeddings, lo=2.0, hi=98.0):
    """Project [H,W,128] float32 embeddings to an [H,W,3] uint8 false-color image via PCA.

    Top-3 principal components -> R,G,B, each percentile-stretched (lo..hi) for contrast.
    Nodata / non-finite / zero-norm pixels -> black (0,0,0).
    """
    h, w, c = embeddings.shape
    flat = np.asarray(embeddings, dtype=np.float32).reshape(-1, c)
    valid = np.isfinite(flat).all(axis=1) & (np.linalg.norm(flat, axis=1) > 0)
    if valid.sum() < 3:
        return np.zeros((h, w, 3), dtype=np.uint8)

    X = flat[valid]
    mean = X.mean(axis=0)
    Xc = X - mean
    cov = (Xc.T @ Xc) / max(len(Xc) - 1, 1)
    _, evecs = np.linalg.eigh(cov)            # ascending eigenvalues
    comps = evecs[:, -3:][:, ::-1]            # top-3 components, descending

    proj = (flat - mean) @ comps              # [N,3]; invalid rows are garbage, masked below
    out = np.zeros((flat.shape[0], 3), dtype=np.uint8)
    valid_proj = proj[valid]
    for k in range(3):
        p_lo, p_hi = np.percentile(valid_proj[:, k], [lo, hi])
        rng = max(float(p_hi - p_lo), 1e-6)
        scaled = np.clip((proj[:, k] - p_lo) / rng, 0.0, 1.0) * 255.0
        out[:, k] = np.nan_to_num(scaled).astype(np.uint8)
    out[~valid] = 0
    return out.reshape(h, w, 3)
