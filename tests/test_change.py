"""Change engine self-check. Run: python tests/test_change.py

Builds two years of a synthetic landscape where two different KINDS of change happen
(forest->bare and bare->water) plus unchanged background, then checks:
  - change_score lights up only where something changed
  - the lazy ChangeFeatures view is correct and head-compatible
  - painting each change type and classifying tells the two kinds APART (which delta-only
    or a single similarity score could not)
"""
import numpy as np

from tessera_paint import head
from tessera_paint.change import ChangeFeatures, change_score, suggest_threshold

_G = np.random.default_rng(7)
_DIRS = _G.normal(0, 1, (4, 64))
_DIRS /= np.linalg.norm(_DIRS, axis=1, keepdims=True)
FOREST, BARE, WATER, CROP = _DIRS


def _years(jitter=0.03, texture=0.12, seed=0):
    """32x32x64 pair. Rows 0-9 forest->bare (clearcut), rows 10-19 bare->water (flooding),
    rows 20-31 unchanged crop.

    Each pixel gets a per-pixel `texture` offset that PERSISTS across both years (local
    variety within a class) plus a small per-year `jitter`. That's how real embeddings behave:
    an unchanged pixel is nearly identical year to year. Independent per-year noise would make
    even unchanged pixels look changed — an artifact of the fixture, not of the data.
    """
    rng = np.random.default_rng(seed)
    tex = rng.normal(0, texture, (32, 32, 64))
    a = tex + rng.normal(0, jitter, (32, 32, 64))
    b = tex + rng.normal(0, jitter, (32, 32, 64))
    a[:10] += FOREST
    b[:10] += BARE          # change type 1
    a[10:20] += BARE
    b[10:20] += WATER       # change type 2
    a[20:] += CROP
    b[20:] += CROP          # unchanged
    return a.astype(np.float32), b.astype(np.float32)


def test_change_score_finds_only_real_change():
    a, b = _years()
    valid = np.ones(a.shape[:2], bool)
    s = change_score(a, b, valid)
    assert s.shape == (32, 32) and s.dtype == np.float32
    assert 0.0 <= s.min() and s.max() <= 1.0
    assert s[:20].mean() > 0.2, "changed rows score high"
    assert s[20:].mean() < 0.05, "unchanged rows score near zero"
    assert s[:20].min() > s[20:].max(), "every changed pixel beats every unchanged one"


def test_change_score_rejects_misaligned():
    a, b = _years()
    try:
        change_score(a, b[:16])
        raise AssertionError("should have refused misaligned years")
    except ValueError as exc:
        assert "aligned" in str(exc)


def test_suggest_threshold_is_between_populations():
    a, b = _years()
    valid = np.ones(a.shape[:2], bool)
    s = change_score(a, b, valid)
    # what matters for UX: the default cutoff hides ALL unchanged pixels without hiding
    # everything, so the map opens showing real change instead of a blank or a wash.
    t = suggest_threshold(s, valid)                 # default percentile
    assert t > s[20:].max(), "default cutoff excludes the unchanged population"
    assert t < s[:20].max(), "default cutoff still shows some changed pixels"


def test_align_identical_grids_is_noop():
    from rasterio.transform import from_origin
    from tessera_paint.change import align
    a, b = _years()
    t = from_origin(500000.0, 5400000.0, 10.0, 10.0)
    A, B, tt = align(a, t, b, t, "EPSG:32610")
    assert A is a and B is b, "matching grids are returned untouched (no resample)"


def test_align_regrids_offset_year_onto_reference():
    """A whole-pixel shift: B's data should land at the right place on A's grid, with the
    non-overlap filled by nodata (NaN)."""
    from rasterio.transform import from_origin
    from tessera_paint.change import align
    a, b = _years()
    ta = from_origin(500000.0, 5400000.0, 10.0, 10.0)
    tb = from_origin(500000.0 + 20.0, 5400000.0 - 10.0, 10.0, 10.0)  # +2 col, +1 row
    A, B, tt = align(a, ta, b, tb, "EPSG:32610")
    assert B.shape == a.shape and tt.c == ta.c and tt.a == ta.a
    # a's pixel (1,2) is the same world cell as b's pixel (0,0)
    assert np.allclose(B[1, 2], b[0, 0])
    # B sits 2 cols east / 1 row south, so A's top-left corner is outside B -> nodata (not 0)
    assert np.isnan(B[0, 0]).all(), "cells B doesn't cover become nodata, not fake zeros"


def test_align_handles_subpixel_resolution_drift():
    """The actual bug: geotessera returns years at slightly different resolution. align must
    resample rather than raise, and the differenced result must be usable."""
    from rasterio.transform import from_origin
    from tessera_paint.change import align
    a, b = _years()
    ta = from_origin(500000.0, 5400000.0, 10.0, 10.0)
    tb = from_origin(500000.0, 5400000.0, 10.002, 10.002)   # 0.02% larger pixels
    A, B, tt = align(a, ta, b, tb, "EPSG:32610")
    assert B.shape == a.shape
    valid = np.isfinite(B).all(axis=2)
    s = change_score(A, np.nan_to_num(B), valid)
    assert s.shape == a.shape[:2]


def test_change_features_lazy_view_is_correct():
    a, b = _years()
    f = ChangeFeatures(a, b)
    assert f.shape == (32, 32, 128)
    block = f[0:4]                                   # row slice, as head.predict uses
    assert block.shape == (4, 32, 128)
    assert np.allclose(block[..., :64], a[0:4])      # baseline half
    assert np.allclose(block[..., 64:], b[0:4] - a[0:4])   # delta half
    rows = np.array([0, 1, 15]); cols = np.array([2, 3, 4])
    pts = f[rows, cols]                              # fancy index, as stroke sampling uses
    assert pts.shape == (3, 128)
    assert np.allclose(pts[0, :64], a[0, 2])


def test_otsu_finds_the_split_between_changed_and_unchanged():
    """Otsu should land the cutoff BETWEEN the two populations without being told anything —
    that's what makes it a user-friendly 'Auto' button."""
    from tessera_paint.change import otsu_threshold
    a, b = _years()
    valid = np.ones(a.shape[:2], bool)
    s = change_score(a, b, valid)
    t = otsu_threshold(s, valid)
    # judge it the way a user would: does the cutoff put (nearly) all changed pixels above
    # and (nearly) all unchanged below? Exact bracketing is too strict — landing right on the
    # boundary is a correct answer.
    assert (s[:20] >= t).mean() > 0.95, f"cutoff {t} keeps the changed pixels"
    assert (s[20:] < t).mean() > 0.95, f"cutoff {t} drops the unchanged pixels"
    # and it adapts: a scene where almost nothing changed still yields a usable cutoff
    a2, b2 = _years(seed=11)
    b2[:] = a2 + 0.001                      # essentially no change anywhere
    s2 = change_score(a2, b2, valid)
    t2 = otsu_threshold(s2, valid)
    assert np.isfinite(t2) and t2 >= 0.0
    assert (s2 >= t2).mean() < 0.5, "a quiet scene doesn't get flagged as mostly changed"


def test_coverage_and_categorize_expose_nodata():
    """Missing data must be its OWN category — never scored 0, which would look identical
    to 'nothing changed'."""
    from tessera_paint.change import (COV_MISSING_A, COV_MISSING_B, COV_MISSING_BOTH,
                                      categorize, coverage)
    a, b = _years()
    va = np.ones((32, 32), bool)
    vb = np.ones((32, 32), bool)
    va[0, :4] = False              # missing in A only
    vb[1, :4] = False              # missing in B only
    va[2, :4] = vb[2, :4] = False  # missing in both

    cov = coverage(va, vb)
    assert (cov[0, :4] == COV_MISSING_A).all()
    assert (cov[1, :4] == COV_MISSING_B).all()
    assert (cov[2, :4] == COV_MISSING_BOTH).all()
    assert (cov[5:] == 0).all(), "pixels present in both years are OK"

    valid = va & vb
    s = change_score(a, b, valid)
    cat = categorize(s, cov, threshold=0.1)
    assert (cat[0, :4] == 2).all() and (cat[1, :4] == 2).all(), "one-year gaps -> category 2"
    assert (cat[2, :4] == 3).all(), "both-year gaps -> category 3"
    assert (cat[5:10] == 1).all(), "real change still labelled changed"
    assert (cat[25:] == 0).all(), "unchanged stays background"
    # the point: a nodata pixel is never confused with an unchanged one
    assert cat[2, 0] != 0 and cat[0, 0] != 0


def test_classify_change_distinguishes_change_types():
    """The payoff: two different KINDS of change get different labels."""
    a, b = _years(seed=3)
    valid = np.ones(a.shape[:2], bool)
    f = ChangeFeatures(a, b)
    rows = np.arange(2, 6)
    clearcut = f[np.repeat(rows, 6), np.tile(np.arange(2, 8), 4)]          # rows 2-5
    flooded = f[np.repeat(rows + 10, 6), np.tile(np.arange(2, 8), 4)]      # rows 12-15
    stable = f[np.repeat(rows + 20, 6), np.tile(np.arange(2, 8), 4)]       # rows 22-25
    h = head.train([clearcut, flooded, stable])
    lab, _ = head.predict(f, valid, h)
    assert (lab[:10] == 0).mean() > 0.95, "clearcut rows -> class 0"
    assert (lab[10:20] == 1).mean() > 0.95, "flooded rows -> class 1"
    assert (lab[20:] == 2).mean() > 0.95, "unchanged rows -> class 2"


if __name__ == "__main__":
    test_change_score_finds_only_real_change()
    test_change_score_rejects_misaligned()
    test_suggest_threshold_is_between_populations()
    test_align_identical_grids_is_noop()
    test_align_regrids_offset_year_onto_reference()
    test_align_handles_subpixel_resolution_drift()
    test_change_features_lazy_view_is_correct()
    test_otsu_finds_the_split_between_changed_and_unchanged()
    test_coverage_and_categorize_expose_nodata()
    test_classify_change_distinguishes_change_types()
    print("ok")
