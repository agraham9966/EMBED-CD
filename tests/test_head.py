"""Portable multi-class head self-check. Run: python tests/test_head.py

Key properties: classes compete (one label/pixel), confidence abstains without relabeling,
and — the point of the rewrite — a head trained on one AREA applies to a DIFFERENT area with
different local statistics, because it works in raw embedding space and carries its own
standardization.
"""
import os
import tempfile

import numpy as np

from tessera_paint import head


def _raw_scene(seed=0, shift=0.0, scale=1.0, noise=0.3):
    """32x32x128 RAW-style cube: three classes along fixed global directions, plus per-scene
    offset/scale (shift, scale) to mimic a DIFFERENT area's local statistics. Rows 30+ nodata."""
    rng = np.random.default_rng(seed)
    g = np.random.default_rng(999)                      # SAME class directions across scenes
    dirs = g.normal(0, 1, (3, 128))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    cube = rng.normal(0, noise, (32, 32, 128)).astype(np.float32)
    cube[:10] += dirs[0] * 2.0
    cube[10:20] += dirs[1] * 2.0
    cube[20:30] += dirs[2] * 2.0
    cube = cube * scale + shift                         # this area's local offset/scale
    cube[30:, :, :] = np.nan
    return cube.astype(np.float32)


def _valid(cube):
    return np.isfinite(cube).all(axis=2)


def test_labels_exclusive_and_correct():
    cube = _raw_scene()
    a = cube[2:5, 2:8].reshape(-1, 128)
    b = cube[12:15, 2:8].reshape(-1, 128)
    c = cube[22:25, 2:8].reshape(-1, 128)
    h = head.train([a, b, c])
    lab, mar = head.predict(cube, _valid(cube), h)
    assert (lab[:10] == 0).mean() > 0.95
    assert (lab[10:20] == 1).mean() > 0.95
    assert (lab[20:30] == 2).mean() > 0.95
    assert (lab[30:] == -1).all()
    assert set(np.unique(lab[:30])).issubset({0, 1, 2})     # exclusive: never two classes/pixel
    assert 0.0 <= mar.min() and mar.max() <= 1.0


def test_head_trained_on_A_applies_to_B():
    """The feature: train on area A, apply to a DIFFERENT area B (same classes, different
    local mean/scale). Portable because it's raw-space + self-standardizing."""
    A = _raw_scene(seed=1, shift=0.0, scale=1.0)
    B = _raw_scene(seed=2, shift=0.7, scale=1.4)          # different area statistics
    h = head.train([A[2:5, 2:8].reshape(-1, 128),
                    A[12:15, 2:8].reshape(-1, 128),
                    A[22:25, 2:8].reshape(-1, 128)])
    lab, _ = head.predict(B, _valid(B), h)               # NB: predict on B, trained on A
    assert (lab[:10] == 0).mean() > 0.9
    assert (lab[10:20] == 1).mean() > 0.9
    assert (lab[20:30] == 2).mean() > 0.9


def test_confidence_abstains_not_reclassifies():
    cube = _raw_scene(3)
    h = head.train([cube[2:5, 2:8].reshape(-1, 128), cube[12:15, 2:8].reshape(-1, 128)])
    lab, mar = head.predict(cube, _valid(cube), h)
    strict = head.apply_confidence(lab, mar, 0.9)
    loose = head.apply_confidence(lab, mar, 0.0)
    assert (strict == -1).sum() > (loose == -1).sum()
    kept = strict != -1
    assert (strict[kept] == lab[kept]).all()


def test_single_sample_per_class():
    cube = _raw_scene(4, noise=0.05)   # one clean representative pixel per class
    h = head.train([cube[2, 2][None, :], cube[12, 2][None, :]])
    lab, _ = head.predict(cube, _valid(cube), h)
    assert (lab[:10] == 0).mean() > 0.8 and (lab[10:20] == 1).mean() > 0.8


def test_save_load_roundtrip():
    cube = _raw_scene(5)
    h = head.train([cube[2:5, 2:8].reshape(-1, 128), cube[12:15, 2:8].reshape(-1, 128)])
    p = os.path.join(tempfile.mkdtemp(), "head.npz")
    head.save(h, p)
    h2 = head.load(p)
    l1, _ = head.predict(cube, _valid(cube), h)
    l2, _ = head.predict(cube, _valid(cube), h2)
    assert np.array_equal(l1, l2)


if __name__ == "__main__":
    test_labels_exclusive_and_correct()
    test_head_trained_on_A_applies_to_B()
    test_confidence_abstains_not_reclassifies()
    test_single_sample_per_class()
    test_save_load_roundtrip()
    print("ok")
