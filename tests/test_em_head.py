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
        loaded, colors, feats = H.load_classes(p)
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


def _same_change_two_baselines(n_per=20, seed=3, sep=3.0):
    """One change TYPE reached from two different starting land covers, plus a distractor.

    The real case: a demolished building and a fresh clearcut both end as bare ground. Under
    [A, B] they share almost nothing, because half the vector is the baseline they came from.
    Under [A, B-A] the shared part is the delta, which is the thing they actually have in
    common.
    """
    rng = np.random.default_rng(seed)
    forest, urban = rng.normal(0, 1, DEPTH), rng.normal(0, 1, DEPTH)
    clearing = rng.normal(0, 1, DEPTH)
    clearing /= np.linalg.norm(clearing)
    clearing *= sep                                   # the SAME movement from either baseline

    def block(a, delta, n):
        return np.concatenate([np.tile(a, (n, 1)), np.tile(a + delta, (n, 1))], axis=1) \
            + rng.normal(0, 0.30, (n, 2 * DEPTH))

    from_forest = block(forest, clearing, n_per)
    from_urban = block(urban, clearing, n_per)
    other = block(forest, -clearing, n_per)           # opposite change: must NOT be swept in
    return (from_forest.astype(np.float32), from_urban.astype(np.float32),
            other.astype(np.float32))


def _same_destination_two_baselines(n_per=20, seed=3):
    """Two baselines reaching the SAME end state — a demolished building and a fresh clearcut
    both becoming bare ground.

    Deliberately NOT the same thing as `_same_change_two_baselines`, whose prose says "both end
    as bare ground" but whose construction adds the same delta to two DIFFERENT baselines, so
    its groups end up somewhere different from each other. That fixture models a shared
    MOVEMENT, which is delta's case; this one models a shared DESTINATION, which is after's.
    Conflating them is how you conclude a mode works when it does not.
    """
    rng = np.random.default_rng(seed)
    forest, urban = rng.normal(0, 1, DEPTH), rng.normal(0, 1, DEPTH)
    bare, water = rng.normal(0, 1, DEPTH), rng.normal(0, 1, DEPTH)

    def block(a, b, n):
        return np.concatenate([np.tile(a, (n, 1)), np.tile(b, (n, 1))], axis=1)             + rng.normal(0, 0.30, (n, 2 * DEPTH))

    return (block(forest, bare, n_per).astype(np.float32),    # forest -> bare
            block(urban, bare, n_per).astype(np.float32),     # urban  -> bare (unseen baseline)
            block(forest, water, n_per).astype(np.float32))   # forest -> water (distractor)


def test_end_state_features_recognise_a_class_on_an_unseen_baseline():
    """The one thing transition mode measurably cannot do, and the reason "after" exists.

    Train on clearings that came from forest, then ask about clearings that came from urban —
    a baseline the model has never seen. Measured over 12 seeds:

        raw 0%      delta 0%      after 99%

    The distance gate is ON throughout. "after" does not win by evading the familiarity test;
    dropping A makes the unseen baseline genuinely in-distribution, so the gate passes it
    correctly. (With the gate off, raw and delta reach 92% and 87% — the information was always
    there, the gate was the binding constraint.)
    """
    got = {}
    for mode in ("raw", "delta", "after"):
        hits = []
        for seed in range(12):
            ff, fu, other = _same_destination_two_baselines(seed=seed)
            x = np.concatenate([ff[:10], other[:10]])
            y = np.array(["cleared"] * 10 + ["distractor"] * 10, dtype=object)
            h = H.OvRHead(features=mode).fit(x, y)
            hits.append((h.predict(fu)[0] == "cleared").mean())
        got[mode] = float(np.mean(hits))
    assert got["after"] > 0.9, f"end-state transfer collapsed to {got['after']:.0%}"
    assert got["delta"] < 0.1, (f"transition mode now transfers at {got['delta']:.0%} — if this "
                                "has genuinely improved, update these numbers everywhere they "
                                "are quoted, starting with head.py's comment")
    print(f"ok unseen baseline: raw {got['raw']:.0%}, delta {got['delta']:.0%}, "
          f"after {got['after']:.0%}")


def test_baseline_plus_delta_handles_a_class_spanning_two_baselines():
    """A class whose members reached the same end state from DIFFERENT starting land cover —
    a demolished building and a fresh clearcut both become bare ground.

    What delta actually buys, measured over 12 seeds: 0.954 -> 0.978 mean accuracy, better on
    8 seeds and worse on 3. Real but modest.

    What it does NOT buy, and this was measured after being asserted wrongly: it does not let a
    class TRANSFER to a baseline it never saw. Both representations score 0% there, because
    [A, B-A] still carries A as half the vector, so an unseen baseline is still an unseen
    vector. Only dropping or downweighting A would change that, and Google's comparison says
    baseline context is what makes these features good in the first place.
    """
    accs = {"raw": [], "delta": []}
    for seed in range(12):
        ff, fu, other = _same_change_two_baselines(seed=seed)
        x = np.concatenate([ff[:5], fu[:5], other[:5]])
        y = np.array(["cleared"] * 10 + ["opposite"] * 5, dtype=object)
        xt = np.concatenate([ff[5:], fu[5:], other[5:]])
        yt = np.array(["cleared"] * 30 + ["opposite"] * 15, dtype=object)
        for mode in accs:
            accs[mode].append((H.OvRHead(features=mode).fit(x, y).predict(xt)[0] == yt).mean())
    raw, delta = np.mean(accs["raw"]), np.mean(accs["delta"])
    assert delta >= raw - 0.01, f"delta {delta:.3f} materially worse than raw {raw:.3f}"
    assert delta > 0.9, f"delta accuracy collapsed to {delta:.3f}"
    print(f"ok class spanning two baselines: raw {raw:.3f} vs delta {delta:.3f} over 12 seeds")


def test_a_single_class_cannot_yet_reject_the_opposite_change():
    """A KNOWN weakness, recorded so it cannot regress further and so the fix has a baseline.

    Label three cleared-forest objects and the single-class path also accepts 100% of the
    REVERSE change from the same baseline — regrowth gets selected as clearing. With one class
    there is nothing to discriminate against, so scoring is cosine to a prototype, and with A
    shared between both groups that cosine is largely measuring "did this start as forest".
    Unchanged by the choice of features: all three fail identically. "after" was predicted to
    fix this and does not — measured at 100% acceptance, same as the others. B for "forest
    cleared" and B for "forest regrown" still share the forest component that dominates the
    cosine, so it is the same failure on a different half of the vector.
    """
    ff, fu, other = _same_change_two_baselines()
    pool = np.concatenate([ff, fu, other])
    for mode in ("raw", "delta", "after"):
        h = H.OvRHead(features=mode).fit(ff[:3], np.array(["cleared"] * 3, dtype=object),
                                         pool=pool)
        assert (h.predict(ff)[0] == "cleared").mean() > 0.9, f"{mode}: lost its own examples"
        opp = (h.predict(other)[0] == "cleared").mean()
        assert opp > 0.5, (f"{mode}: opposite-change acceptance is {opp:.0%} — if this has been "
                           "FIXED, update this test rather than deleting it")
    print("ok single-class opposite-change weakness still reproduces (documented, not fixed)")


def test_features_default_is_baseline_plus_delta():
    """Guards the default itself: switching it back to "raw" silently changes what every class
    a user has ever saved will mean."""
    assert H.OvRHead().features == "delta"
    x, y, _ = _planted()
    assert H.fit_from_classes({c: x[y == c] for c in set(y.tolist())}).features == "delta"
    print("ok baseline+delta is the default representation")


def test_an_unknown_features_value_is_refused_not_silently_ignored():
    """This one cost a full measurement cycle. `_transform` only branched on "delta", so any
    other value fell through to "raw" — and a mode that had not been implemented yet measured
    as byte-identical to one that had, over a page of plausible numbers. Fail loudly instead."""
    for bad in ("after_delta", "END", "", None):
        try:
            H.OvRHead(features=bad)
        except ValueError:
            continue
        raise AssertionError(f"features={bad!r} was accepted")
    print("ok an unrecognised features value raises instead of silently meaning raw")


def test_predicting_on_a_different_embedding_width_raises():
    """Stored vectors are [A, B] and the split is shape[1]//2, so a half-width input would be
    silently re-split and answered with confident nonsense."""
    x, y, _ = _planted()
    h = H.OvRHead().fit(x, y)
    try:
        h.predict(x[:, :x.shape[1] // 2])
    except ValueError:
        print("ok a width that does not match the fit raises")
        return
    raise AssertionError("predicting on half-width vectors did not raise")


def test_a_preset_remembers_which_question_it_was_answering():
    """The vectors load under any mode — deliberately — so the file is the only place that can
    say what the names meant. Files written before modes existed report "delta", which is what
    they were in fact labelled under."""
    import json
    x, y, _ = _planted()
    classes = {c: x[y == c] for c in set(y.tolist())}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.json")
        H.save_classes(p, classes, features="after")
        assert H.load_classes(p)[2] == "after"
        old = os.path.join(d, "old.json")
        payload = json.load(open(p))
        del payload["features"]
        json.dump(payload, open(old, "w"))
        assert H.load_classes(old)[2] == "delta", "a pre-modes preset must read as delta"
    print("ok presets carry their mode; older ones read as delta")


def test_review_order_honours_locked():
    """The work-list must be able to exclude what the user has already answered.

    `locked` has been part of this signature since the port and the UI passed nothing for it,
    so "least certain first" kept walking back over objects that were already settled. Unit
    test here as well as in the panel, because the parameter is the contract.
    """
    x, y, _ = _planted()
    h = H.OvRHead().fit(x, y)
    pred, scores = h.predict(x)
    everything = list(H.review_order(pred, scores))
    assert sorted(everything) == list(range(len(x))), "unlocked must offer every row"

    locked = np.zeros(len(x), bool)
    locked[[0, 1, 2]] = True
    rest = list(H.review_order(pred, scores, locked=locked))
    assert not ({0, 1, 2} & set(rest)), f"locked rows came back anyway: {rest[:6]}"
    assert sorted(rest) == list(range(3, len(x))), "locking dropped rows it should have kept"

    assert list(H.review_order(pred, scores, locked=np.ones(len(x), bool))) == [], \
        "everything locked must yield an empty work-list, not a full one"
    print("ok review_order excludes locked rows and keeps the rest")


def test_best_guess_never_abstains():
    """The control says "prefer a best guess over unknown", so it must not return unknown.

    It used to also require clearing a 0.5 floor AND passing the distance gate, so the mode
    whose entire purpose is forcing a decision still declined to make one. That is not a
    subtlety, it is the label being untrue.
    """
    x, y, _ = _planted()
    head = H.OvRHead(decision="argmax").fit(x, y)
    alien = np.random.default_rng(7).normal(20, 1, (40, 2 * DEPTH)).astype(np.float32)
    for name, data in (("its own training data", x), ("objects nothing like it", alien)):
        pred, _ = head.predict(data)
        assert not (pred == H.UNKNOWN).any(), \
            f"best-guess returned unknown for {name}: {(pred == H.UNKNOWN).sum()} of {len(data)}"
    assert set(head.predict(alien)[0]) <= set(head.classes)
    print("ok best guess always commits, even on objects it has never seen anything like")


def test_strictness_moves_the_bar_with_more_than_one_class():
    """The slider was inert whenever a second class existed.

    Each class's bar was `max(out-of-fold quantile, 0.5)`, and on real data the quantile never
    cleared 0.5 — so thresholds sat pinned there across the entire slider range and the unknown
    count did not move (measured on 817 polygons: 27 at q=0, 27 at q=0.40). A control that does
    nothing is worse than no control, because it invites the conclusion that the model is
    unresponsive.
    """
    # Train on half and judge the OTHER half. In-sample, logistic regression drives its own
    # positives to ~0.999, so no reachable bar bites and the slider looks inert for the wrong
    # reason — the same trap the out-of-fold calibration exists to avoid.
    x, y, _ = _planted(n_per=16, sep=1.2)          # deliberately overlapping, so a bar can bite
    train = np.zeros(len(y), bool)
    train[::2] = True
    unknowns, bars = [], []
    for q in (0.05, 0.15, 0.25, 0.35, 0.40):
        head = H.OvRHead(abstain_quantile=q).fit(x[train], y[train])
        pred, _ = head.predict(x[~train])
        unknowns.append(int((pred == H.UNKNOWN).sum()))
        bars.append(round(min(head.thr.values()), 3))

    assert bars == sorted(bars) and bars[-1] > bars[0] + 0.2, \
        f"the confidence bar barely moved across the slider: {bars}"
    assert unknowns == sorted(unknowns), f"strictness should never REDUCE unknowns: {unknowns}"
    assert unknowns[-1] > unknowns[0], \
        f"the strictest setting abstained no more than the loosest: {unknowns}"
    print(f"ok strictness has traction with several classes: bars {bars}, unknowns {unknowns}")


def test_strictness_still_works_with_a_single_class():
    """The one mode where it always did work — it must not have been broken by giving the
    multi-class path a floor."""
    x, y, _ = _planted()
    one = {"cutblock": x[y == "cutblock"][:3]}
    counts = []
    for q in (0.0, 0.2, 0.4):
        head = H.fit_from_classes(one, pool=x, abstain_quantile=q)
        counts.append(int((head.predict(x)[0] == H.UNKNOWN).sum()))
    assert counts == sorted(counts) and counts[-1] > counts[0], \
        f"single-class strictness stopped responding: {counts}"
    print(f"ok single-class strictness still responds: unknowns {counts}")


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
    test_baseline_plus_delta_handles_a_class_spanning_two_baselines()
    test_a_single_class_cannot_yet_reject_the_opposite_change()
    test_features_default_is_baseline_plus_delta()
    test_review_order_honours_locked()
    test_a_preset_remembers_which_question_it_was_answering()
    test_predicting_on_a_different_embedding_width_raises()
    test_an_unknown_features_value_is_refused_not_silently_ignored()
    test_end_state_features_recognise_a_class_on_an_unseen_baseline()
    test_best_guess_never_abstains()
    test_strictness_moves_the_bar_with_more_than_one_class()
    test_strictness_still_works_with_a_single_class()
    print("all ok")
