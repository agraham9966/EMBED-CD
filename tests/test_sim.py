"""Engine self-check. Run: python tests/test_sim.py  (no framework needed).

Plants a cluster in a synthetic int8 embedding cube, seeds a pixel inside it, and
asserts the mask selects the cluster and excludes everything else. Fails loudly if
the core cosine-similarity / dequantization / masking math breaks.
"""
import numpy as np

from tessera_paint.sim import similarity, mask
from tessera_paint.viz import pca_rgb


def _synthetic():
    """8x8x128 float32 cube (as fetch_mosaic_for_region returns): top-left 4x4 points along
    channel 0, background along channel 1. A zero pixel at (7,7) and a NaN pixel at (6,6)
    exercise the zero-norm and non-finite guards (mosaic edges can be nodata)."""
    h = w = 8
    cube = np.zeros((h, w, 128), dtype=np.float32)
    cube[:4, :4, 0] = 1.0      # cluster A
    cube[4:, :, 1] = 1.0       # background
    cube[:4, 4:, 1] = 1.0      # background
    cube[7, 7, :] = 0.0        # zero pixel (norm 0)
    cube[6, 6, :] = np.nan     # nodata pixel
    return cube


def test_similarity_and_mask_select_cluster():
    cube = _synthetic()
    seed = cube[0, 0].copy()   # a pixel inside cluster A

    sim = similarity(cube, [seed])
    assert sim.shape == (8, 8)
    assert sim.dtype == np.float32
    assert np.isfinite(sim).all(), "zero-norm and NaN pixels must not leak non-finite values"
    assert sim.min() >= 0.0 and sim.max() <= 1.0, "scores normalized to [0,1]"

    # cluster ~1.0, background ~0.0
    assert sim[0, 0] > 0.9
    assert sim[3, 3] > 0.9
    assert sim[5, 5] < 0.1

    m = mask(sim, threshold=0.5)
    assert m.dtype == np.uint8
    assert m[:4, :4].all(), "whole cluster selected"
    assert not m[4:, :].any(), "no background selected"
    assert not m[:4, 4:].any(), "no background selected"
    assert m[7, 7] == 0, "zero pixel not selected"
    assert m[6, 6] == 0, "nodata pixel not selected"


def test_multi_seed_uses_mean():
    cube = _synthetic()
    sim = similarity(cube, [cube[0, 0], cube[1, 1]])  # two seeds from the cluster
    assert sim[2, 2] > 0.9
    assert sim[6, 6] < 0.1 if np.isfinite(sim[6, 6]) else True


def test_negative_exclude_refines_not_nukes():
    cube = _synthetic()
    pos = cube[0, 0]          # cluster direction
    neg = cube[5, 0]          # background direction
    sim = similarity(cube, [pos], neg_vectors=[neg])
    # the whole point: adding an exclude must NOT delete the class
    assert sim[1, 1] > 0.5,   "cluster stays strongly selected after an exclude"
    assert sim[5, 5] < 0.1,   "excluded background is suppressed"
    m = mask(sim, threshold=0.5)
    assert m[:4, :4].all(), "cluster survives the exclude"
    assert not m[4:, :].any(), "background removed"


def test_stats_reusable_across_calls():
    from tessera_paint.sim import standardize_stats
    cube = _synthetic()
    st = standardize_stats(cube)
    s1 = similarity(cube, [cube[0, 0]], stats=st)
    s2 = similarity(cube, [cube[0, 0]])
    assert np.allclose(s1, s2, atol=1e-5), "passing precomputed stats matches computing them"


def test_prepare_and_score_match_full_path():
    from tessera_paint.sim import prepare, score
    cube = _synthetic()
    znorm, valid, nodata = prepare(cube.copy())
    assert znorm.dtype == np.float32
    assert not valid[6, 6] and not valid[7, 7]      # nan and zero pixels invalid
    seed = znorm[0, 0]                               # prepared cluster-A pixel
    sim = score(znorm, valid, [seed])
    assert sim.min() >= 0.0 and sim.max() <= 1.0
    assert sim[0, 0] > 0.9 and sim[3, 3] > 0.9       # cluster selected
    assert sim[5, 5] < 0.1                            # background not
    assert sim[6, 6] == 0.0 and sim[7, 7] == 0.0      # nodata -> 0


def test_score_negative_refines_not_nukes():
    from tessera_paint.sim import prepare, score
    znorm, valid, _ = prepare(_synthetic())
    sim = score(znorm, valid, [znorm[0, 0]], neg_vectors=[znorm[5, 0]])
    assert sim[1, 1] > 0.5, "cluster survives the exclude"
    assert sim[5, 5] < 0.1, "excluded background suppressed"


def test_ndarray_seed_inputs():
    """The dock passes numpy ARRAYS of stroke pixels (not lists). `if ndarray:` raises, so
    every seed-collection check must use len()/is-None. Two shipped crashes came from this —
    call the engine exactly the way the plugin does."""
    from tessera_paint.sim import prepare, score, similarity
    znorm, valid, _ = prepare(_synthetic())
    pos = np.asarray([znorm[0, 0], znorm[1, 1]], dtype=np.float32)   # [n,128] ndarray
    neg = np.asarray([znorm[5, 0], znorm[6, 0]], dtype=np.float32)
    empty = np.empty((0, 128), np.float32)

    assert score(znorm, valid, pos).shape == (8, 8)
    assert score(znorm, valid, pos, neg_vectors=neg).shape == (8, 8)
    assert score(znorm, valid, pos, neg_vectors=empty).shape == (8, 8)   # empty == no negatives
    assert score(znorm, valid, pos, neg_vectors=None).shape == (8, 8)
    # the older full path takes ndarrays too
    cube = _synthetic()
    raw_pos = np.asarray([cube[0, 0], cube[1, 1]], dtype=np.float32)
    raw_neg = np.asarray([cube[5, 0]], dtype=np.float32)
    assert similarity(cube, raw_pos, neg_vectors=raw_neg).shape == (8, 8)
    assert similarity(cube, raw_pos, neg_vectors=empty).shape == (8, 8)


def test_pca_rgb_distinguishes_clusters():
    cube = _synthetic()
    rgb = pca_rgb(cube)
    assert rgb.shape == (8, 8, 3)
    assert rgb.dtype == np.uint8
    # the two planted clusters should get visibly different colors
    a = rgb[0, 0].astype(int)   # cluster A
    b = rgb[5, 5].astype(int)   # background
    assert np.abs(a - b).sum() > 30, "clusters should map to distinct colors"
    assert tuple(rgb[6, 6]) == (0, 0, 0), "nodata pixel is black"


if __name__ == "__main__":
    test_similarity_and_mask_select_cluster()
    test_multi_seed_uses_mean()
    test_negative_exclude_refines_not_nukes()
    test_stats_reusable_across_calls()
    test_prepare_and_score_match_full_path()
    test_ndarray_seed_inputs()
    test_score_negative_refines_not_nukes()
    test_pca_rgb_distinguishes_clusters()
    print("ok")
