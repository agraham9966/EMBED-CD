"""The editable head. Two properties matter more than accuracy:

  - it can say "unknown", so a user's classes never have to cover the world exhaustively
  - adding a class does not disturb the classes already there
Plus: it must agree with the sklearn original it was ported from (checked when sklearn is
available, skipped when it isn't).
Run: python tests/test_em_head.py
"""
import os
import tempfile

import numpy as np

from embed_cd import head as H

DEPTH = 64


def _planted(n_per=25, seed=0, sep=3.0):
    """Three separable change types in a 128-d [A, B] space."""
    rng = np.random.default_rng(seed)
    centres = {}
    base = rng.normal(0, 1, DEPTH)
    for i, name in enumerate(("cutblock", "burn", "water")):
        after = base.copy()
        after[i * 8:(i + 1) * 8] += sep          # each type moves a different part of the vector
        centres[name] = np.concatenate([base, after])
    x, y = [], []
    for name, c in centres.items():
        x.append(c + rng.normal(0, 0.35, (n_per, 2 * DEPTH)))
        y += [name] * n_per
    return np.concatenate(x).astype(np.float32), np.array(y, dtype=object), centres


def test_recovers_planted_classes():
    x, y, _ = _planted()
    head = H.OvRHead().fit(x, y)
    pred, _ = head.predict(x)
    acc = (pred == y).mean()
    assert acc > 0.95, acc
    print(f"ok planted classes recovered at {100*acc:.0f}% accuracy")


def test_out_of_distribution_returns_unknown():
    """The property that makes this usable: something the user never labelled must not be
    forced into their nearest class."""
    x, y, _ = _planted()
    head = H.OvRHead().fit(x, y)
    rng = np.random.default_rng(7)
    alien = rng.normal(6.0, 0.4, (20, 2 * DEPTH)).astype(np.float32)
    pred, _ = head.predict(alien)
    share = (pred == H.UNKNOWN).mean()
    assert share > 0.8, f"only {100*share:.0f}% of alien objects abstained"
    known, _ = head.predict(x)
    assert (known == H.UNKNOWN).mean() < 0.2, "abstention must not swallow the training classes"
    print(f"ok {100*share:.0f}% of out-of-distribution objects come back as unknown")


def test_an_unlabelled_change_type_mostly_abstains():
    """The realistic case, and a harder one than a wild outlier: a fourth genuine change type
    at the same scale as the others that the user simply never labelled. It should mostly come
    back as unknown rather than being forced into the nearest labelled class."""
    x, y, centres = _planted()
    head = H.OvRHead().fit(x, y)
    rng = np.random.default_rng(23)
    base = centres["cutblock"][:DEPTH]
    after = base.copy()
    after[40:48] += 3.0                       # a direction no labelled class occupies
    novel = (np.concatenate([base, after])
             + rng.normal(0, 0.35, (30, 2 * DEPTH))).astype(np.float32)
    pred, _ = head.predict(novel)
    share = (pred == H.UNKNOWN).mean()
    assert share > 0.5, f"only {100*share:.0f}% of an unlabelled type abstained"
    print(f"ok {100*share:.0f}% of an unlabelled change type abstains")


def test_one_class_finds_more_like_these():
    """The state every session starts in: the user labels a few cutblocks and expects the other
    cutblocks to light up. One-vs-rest has no 'rest' yet, so this must fall back to similarity
    rather than refusing to answer — refusing is what made the first build look broken."""
    x, y, _ = _planted()
    labelled = np.flatnonzero(y == "cutblock")[:3]
    head = H.fit_from_classes({"cutblock": x[labelled]}, pool=x)
    assert head is not None and head.single_class
    pred, scores = head.predict(x)

    truth = y == "cutblock"
    found = pred == "cutblock"
    recall = (found & truth).sum() / truth.sum()
    wrong = (found & ~truth).sum()
    assert recall > 0.8, f"only found {100*recall:.0f}% of the other cutblocks"
    assert wrong == 0, f"{wrong} objects of other types wrongly called cutblock"
    # and the score has to RANK, so a review list is meaningful
    assert scores[0][truth].mean() > scores[0][~truth].mean() + 0.05
    print(f"ok one class: found {100*recall:.0f}% of the rest, {wrong} false positives")


def test_review_order_puts_the_least_trustworthy_first():
    x, y, _ = _planted()
    head = H.OvRHead().fit(x, y)
    pred, scores = head.predict(x)
    rng = np.random.default_rng(5)
    alien = rng.normal(6.0, 0.4, (6, 2 * DEPTH)).astype(np.float32)
    allx = np.concatenate([x, alien])
    pred, scores = head.predict(allx)

    order = H.review_order(pred, scores)
    assert len(order) == len(allx)
    first = order[:6]
    assert (np.asarray(pred, dtype=object)[first] == H.UNKNOWN).all(), \
        "abstentions must come first — those are the ones the head had no opinion on"
    assert set(first.tolist()) == set(range(len(x), len(allx))), first

    locked = np.zeros(len(allx), bool)
    locked[list(first)] = True
    order2 = H.review_order(pred, scores, locked)
    assert not set(order2.tolist()) & set(first.tolist()), "locked items leave the queue"
    print("ok review order: abstentions first, then smallest margin, locked excluded")


def test_a_couple_of_examples_per_class_does_not_abstain_on_everything():
    """The distance gate protects against genuinely novel objects, but a radius measured from
    two hand-picked examples describes THOSE OBJECTS, not the class — so it rejects the class's
    own members. Measured on a real scene it left 18 of 27 objects unknown against 1 with the
    gate off, which is what 'getting a lot of unknowns' looked like from the outside."""
    x, y, _ = _planted()
    few = {c: x[y == c][:2] for c in ("cutblock", "burn", "water")}

    head = H.fit_from_classes(few, pool=x)
    pred, _ = head.predict(x)
    unknown = (pred == H.UNKNOWN).mean()
    correct = (pred == y).mean()

    forced = H.fit_from_classes(few, pool=x, min_for_gate=0)     # gate every class regardless
    pred2, _ = forced.predict(x)
    forced_unknown = (pred2 == H.UNKNOWN).mean()

    assert correct > 0.7, f"only {100*correct:.0f}% correct from 2 examples per class"
    assert unknown < 0.3, f"{100*unknown:.0f}% unknown from 2 examples per class"
    assert forced_unknown > 3 * unknown, (
        "gating on a 2-example radius should be dramatically worse, or this test is not "
        f"measuring the thing it claims ({100*forced_unknown:.0f}% vs {100*unknown:.0f}%)")
    print(f"ok 2 examples/class: {100*unknown:.0f}% unknown, {100*correct:.0f}% correct "
          f"— against {100*forced_unknown:.0f}% unknown if the gate is forced on")


def test_the_gate_still_engages_once_a_class_is_well_described():
    """It must not be disabled outright — with enough examples the radius is meaningful and is
    the only thing that catches an object no detector should recognise."""
    x, y, _ = _planted(n_per=25)
    head = H.OvRHead().fit(x, y)
    assert head._near(x) is not None, "the gate must be active at 25 examples per class"
    rng = np.random.default_rng(7)
    alien = rng.normal(6.0, 0.4, (20, 2 * DEPTH)).astype(np.float32)
    assert (head.predict(alien)[0] == H.UNKNOWN).mean() > 0.8
    print("ok the gate re-engages once classes have enough examples to describe them")


def test_adding_a_class_leaves_the_others_alone():
    x, y, centres = _planted()
    head = H.OvRHead().fit(x, y)
    before = head.scores(x)[head.classes.index("cutblock")]

    rng = np.random.default_rng(11)
    extra_c = np.concatenate([centres["cutblock"][:DEPTH], centres["cutblock"][DEPTH:] + 4.0])
    extra = (extra_c + rng.normal(0, 0.35, (25, 2 * DEPTH))).astype(np.float32)
    x2 = np.concatenate([x, extra])
    y2 = np.concatenate([y, np.array(["road"] * 25, dtype=object)])
    head2 = H.OvRHead().fit(x2, y2)
    after = head2.scores(x)[head2.classes.index("cutblock")]

    assert "road" in head2.classes and len(head2.classes) == 4
    # one-vs-rest, so cutblock's detector saw new negatives but is still fundamentally the same
    assert np.corrcoef(before, after)[0, 1] > 0.9, np.corrcoef(before, after)[0, 1]
    print("ok adding a class keeps the existing detectors' behaviour")


def test_preset_roundtrip_refits_to_the_same_predictions():
    x, y, _ = _planted()
    classes = {c: x[y == c] for c in ("cutblock", "burn", "water")}
    head = H.fit_from_classes(classes)
    pred, _ = head.predict(x)
    with tempfile.TemporaryDirectory() as d:
        p = H.save_classes(os.path.join(d, "c.json"), classes,
                           colors={"cutblock": "#ff0000"})
        loaded, colors = H.load_classes(p)
    assert colors["cutblock"] == "#ff0000"
    head2 = H.fit_from_classes(loaded)
    pred2, _ = head2.predict(x)
    assert (pred == pred2).all(), "a saved preset must refit to identical predictions"
    print("ok preset round-trips and refits to identical predictions")


def test_works_with_a_handful_of_labels():
    """The real usage: a few clicks per class, not hundreds."""
    x, y, _ = _planted(n_per=4, seed=3)
    head = H.OvRHead().fit(x, y)
    pred, _ = head.predict(x)
    assert (pred == y).mean() >= 0.75, (pred, y)
    print("ok fits and predicts from 4 examples per class")


def test_delta_features_are_available_and_change_nothing_structurally():
    x, y, _ = _planted()
    head = H.OvRHead(features="delta").fit(x, y)
    pred, _ = head.predict(x)
    assert (pred == y).mean() > 0.95
    print("ok baseline+delta feature form works as a switch")


def test_matches_sklearn_when_available():
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        print("-- sklearn absent (as it is in QGIS); parity check skipped")
        return
    rng = np.random.default_rng(0)
    for n, d, pos in ((60, 128, 12), (40, 128, 8)):
        xx = rng.normal(0, 1, (n, d))
        yy = np.zeros(n, int)
        yy[np.argsort(xx @ rng.normal(0, 1, d))[-pos:]] = 1
        xs = (xx - xx.mean(0)) / xx.std(0)
        sk = LogisticRegression(max_iter=5000, class_weight="balanced", C=1.0).fit(xs, yy)
        b, w = H._fit_logistic(xs, yy, C=1.0)
        p_sk = sk.predict_proba(xs)[:, 1]
        p_ours = H._sigmoid(xs @ w + b)
        assert np.abs(p_sk - p_ours).max() < 0.02, np.abs(p_sk - p_ours).max()
        assert ((p_ours >= 0.5) == (p_sk >= 0.5)).all(), "decisions must match exactly"
    print("ok matches sklearn's LogisticRegression on decisions, ~1e-3 on probabilities")


if __name__ == "__main__":
    test_recovers_planted_classes()
    test_out_of_distribution_returns_unknown()
    test_an_unlabelled_change_type_mostly_abstains()
    test_one_class_finds_more_like_these()
    test_review_order_puts_the_least_trustworthy_first()
    test_a_couple_of_examples_per_class_does_not_abstain_on_everything()
    test_the_gate_still_engages_once_a_class_is_well_described()
    test_adding_a_class_leaves_the_others_alone()
    test_preset_roundtrip_refits_to_the_same_predictions()
    test_works_with_a_handful_of_labels()
    test_delta_features_are_available_and_change_nothing_structurally()
    test_matches_sklearn_when_available()
    print("all ok")
