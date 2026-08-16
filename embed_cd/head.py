"""The editable head: per-class one-vs-rest detectors that are allowed to say "unknown".

A port of the validated `OvRHead` from the activelearning_olmo prototype (macro-F1 ~0.97 on
agriculture / burnscar / cutblock / road), with sklearn replaced by numpy + scipy so the plugin
installs on a stock QGIS with no pip step. The replacement was checked against sklearn on the
shapes this actually sees: probabilities agree to ~1e-3 and **every** prediction matches.

Why one-vs-rest rather than one softmax:
  - a class can fire on nothing it recognises, so "unknown" is a real answer rather than the
    least-bad of a fixed set. That is the whole point when a user's classes never cover a
    landscape exhaustively.
  - adding a class fits one new detector and leaves the others untouched.
  - it refits in well under a second on a few hundred vectors, so the map can recolour as fast
    as the user can click.

Thresholds are calibrated OUT OF FOLD. In sample, logistic regression drives its own positives'
scores to ~0.999, so an in-sample quantile would set a bar that correct held-out predictions
cannot clear. The 0.5 floor exists because a detector should never "fire" while believing the
example is more likely NOT its class — without it, two or three ambiguous training examples can
drag the bar near zero and the detector then fires on everything.
"""
import json

import numpy as np

UNKNOWN = "unknown"
DEFAULT_Q = 0.05      # the strictness slider's resting position


def _fit_logistic(x, y, C=1.0, max_iter=500):
    """L2-regularised logistic regression with balanced class weights.

    Minimises `0.5*w'w + C * sum_i s_i * logloss_i` with the intercept unregularised — sklearn's
    own objective, so results are interchangeable with the prototype's.
    """
    from scipy.optimize import minimize

    n, d = x.shape
    y = np.asarray(y, np.float64)
    counts = np.bincount(y.astype(int), minlength=2).astype(np.float64)
    s = np.where(y == 1, n / (2 * max(counts[1], 1)), n / (2 * max(counts[0], 1)))
    sign = 2 * y - 1

    def obj(theta):
        b, w = theta[0], theta[1:]
        z = x @ w + b
        yz = sign * z
        loss = np.sum(s * np.logaddexp(0.0, -yz))
        gz = -s * sign / (1.0 + np.exp(yz))
        g = np.empty_like(theta)
        g[0] = C * gz.sum()
        g[1:] = C * (x.T @ gz) + w
        return 0.5 * w @ w + C * loss, g

    r = minimize(obj, np.zeros(d + 1), jac=True, method="L-BFGS-B",
                 options={"maxiter": max_iter, "ftol": 1e-12, "gtol": 1e-9})
    return float(r.x[0]), r.x[1:]


def _stratified_folds(y, k, seed=0):
    """Fold assignment keeping each class's proportion — sklearn's StratifiedKFold, in eight
    lines. Deals each class's shuffled members round-robin into folds."""
    rng = np.random.default_rng(seed)
    fold = np.empty(len(y), int)
    for c in np.unique(y):
        idx = np.flatnonzero(y == c)
        rng.shuffle(idx)
        fold[idx] = np.arange(len(idx)) % k
    return fold


class OvRHead:
    """Per-class binary detectors on standardized vectors, with abstention.

    `abstain_quantile` q is the "strictness" control, and it sets each class's confidence bar
    two ways, whichever is higher: the q-quantile of that class's own out-of-fold scores, and a
    floor that rises with q. The second is what gives it traction — on real data the quantile
    alone never cleared `min_confidence`, so with more than one class the control did nothing at
    all (measured: 27 unknowns at q=0 and 27 at q=0.40, thresholds pinned at 0.5 throughout).

    `decision="argmax"` disables abstention entirely: every object gets its best class.
    """

    def __init__(self, abstain_quantile=0.05, calibrate_folds=5, min_confidence=0.5,
                 C=1.0, decision="threshold", features="delta",
                 ood_scale=1.6, min_for_gate=5):
        self.q = abstain_quantile
        self.calibrate_folds = calibrate_folds
        self.min_confidence = min_confidence
        self.C = C
        self.decision = decision                  # "threshold" (can abstain) or "argmax" (cannot)
        # "delta" = [A, B-A]; "raw" = [A, B] exactly as the cell store holds it.
        #
        # Baseline+delta is the default because a class is usually a KIND OF CHANGE, and naming
        # the change explicitly beats making the model infer it from two absolute states.
        # Google's comparison of embedding configurations found the same, as did this codebase
        # independently in tessera_paint/change.py — hence baseline AND delta, not delta alone.
        #
        # What it is actually worth, measured over 12 seeds rather than assumed: for a class
        # whose members reached the same end state from DIFFERENT starting land cover, mean
        # accuracy 0.954 -> 0.978 (better on 8 seeds, worse on 3). On cleanly separated classes
        # the two are indistinguishable. Real, modest, never materially worse.
        #
        # What it does NOT do — asserted here first, then disproved by the test that now guards
        # it: transfer a class to a baseline it never saw. Both forms score 0%, because A is
        # still half the vector, so an unseen baseline is an unseen vector however the other
        # half is written. Only dropping or downweighting A would change that, and baseline
        # context is the thing that makes these features good to begin with.
        #
        # On magnitude, since the single-class path takes cosine on UN-standardized vectors and
        # a tiny delta half would simply be swamped: on 817 real polygons ||B-A|| is 47% of
        # ||A||, and prototype similarity correlates 0.876 with baseline-only under [A, B-A]
        # against 0.901 under [A, B]. Both lean on the baseline; delta leans slightly less.
        #
        # Switchable, and presets are unaffected either way: save_classes stores the raw stored
        # vectors and this transform is applied at fit/predict time, so a class set saved under
        # one setting still loads under the other.
        self.features = features
        # A detector's score alone cannot recognise the unfamiliar. Logistic regression is
        # linear and unbounded, so an object far outside everything the user ever labelled still
        # scores high on whichever detector its direction happens to align with — measured, 0% of
        # obviously-alien objects abstained on score alone. So a class also has to be NEAR the
        # examples that defined it. `ood_scale` widens each class's accepted radius; larger is
        # more permissive. Set to None to disable and go back to scores only.
        self.ood_scale = ood_scale
        # ...but a radius measured from two or three hand-picked objects describes THOSE
        # OBJECTS, not the class, and it then rejects the class's own members. Measured on a
        # real scene with 2 examples per class: the gate left 18 of 27 objects unknown, against
        # 1 with the gate off. Below this many examples there is no spread estimate worth
        # having, so the score bar is left to do the work alone.
        self.min_for_gate = min_for_gate
        self.classes = []
        self.mean_ = self.std_ = None
        self.dets = {}
        self.thr = {}
        self.oof_pos = {}
        self.centroid_ = {}
        self.radius_ = {}
        self.n_examples_ = {}

    # ---------- feature handling ----------
    def _transform(self, x):
        x = np.asarray(x, np.float64)
        if self.features == "delta":
            half = x.shape[1] // 2
            a, b = x[:, :half], x[:, half:]
            x = np.concatenate([a, b - a], axis=1)
        return x

    def _standardize(self, x, fit=False):
        x = self._transform(x)
        if fit:
            self.mean_ = x.mean(axis=0)
            self.std_ = x.std(axis=0)
            self.std_[self.std_ < 1e-12] = 1.0
        return (x - self.mean_) / self.std_

    def _strict_floor(self):
        """Strictness as a confidence bar: `min_confidence` at the slider's DEFAULT, rising to
        ~0.89 at its top.

        Anchored at the default rather than at zero, because measuring from zero made the
        default itself stricter than before and cost real accuracy — a guarded case of two
        examples per class fell from 77% correct to 65%. A control gains traction by moving
        away from where it rests, not by shifting the resting point.

        Never reaches 1.0: a bar nothing can clear is a broken control, not a strict one.
        """
        over = max(0.0, self.q - DEFAULT_Q)
        return self.min_confidence + (1.0 - self.min_confidence) * min(over / 0.45, 0.8)

    # ---------- fitting ----------
    def _oof_positive_scores(self, xs, yc, seed):
        n_pos = int(yc.sum())
        folds = min(self.calibrate_folds, n_pos, int((yc == 0).sum()))
        if folds < 2:                       # too few to cross-validate; fall back in sample
            b, w = _fit_logistic(xs, yc, self.C)
            return _sigmoid(xs[yc == 1] @ w + b)
        fold = _stratified_folds(yc, folds, seed)
        oof = np.full(len(yc), np.nan)
        for k in range(folds):
            te = fold == k
            tr = ~te
            if len(np.unique(yc[tr])) < 2:
                continue
            b, w = _fit_logistic(xs[tr], yc[tr], self.C)
            oof[te] = _sigmoid(xs[te] @ w + b)
        pos = oof[yc == 1]
        pos = pos[np.isfinite(pos)]
        if pos.size == 0:
            b, w = _fit_logistic(xs, yc, self.C)
            pos = _sigmoid(xs[yc == 1] @ w + b)
        return pos

    def _one_class_threshold(self, member_sims, raw, proto, pool):
        """Where to cut a similarity ranking when there is nothing to contrast against.

        The labelled examples alone can't say: three hand-picked objects sit tightly around
        their own mean, so any quantile of THEIR similarity is near 1 and excludes the very
        objects the user is hunting for (measured: 8% of them found). What can say is the
        candidates themselves — the split between "like these" and "not like these" is a break
        in the pool's own similarity distribution, so take Otsu of that.
        """
        floor = float(member_sims.min()) if member_sims.size else 0.5
        if pool is None or len(pool) < 4:
            return max(0.0, floor - 0.05)
        sims = _proto_score(self._transform(pool), proto)
        lo, hi = float(sims.min()), float(sims.max())
        if hi - lo < 1e-6:
            return max(0.0, floor - 0.05)
        hist, edges = np.histogram(sims, bins=64, range=(lo, hi))
        # Prefer a real valley: the widest EMPTY stretch below the labelled examples. That is
        # the honest boundary between "like these" and "not", and unlike Otsu it cannot land
        # inside a cluster — Otsu's cut fell one bin short of the group it was excluding and
        # let two through. Empty-run detection is also stable as the number of candidates grows,
        # where the largest gap between adjacent values drifts into the sparse tails.
        best = None
        run = None
        for i, count in enumerate(list(hist) + [1]):        # sentinel closes a trailing run
            if count == 0:
                run = i if run is None else run
                continue
            if run is not None:
                width, top = i - run, float(edges[i])
                if top <= floor and (best is None or width > best[0]):
                    best = (width, 0.5 * (float(edges[run]) + top))
                run = None
        cut = best[1] if best else lo + _otsu(hist) * (hi - lo)
        cut = min(cut, floor)
        # `q` is a quantile for the discriminative detectors; here it is the same user-facing
        # idea — strictness — expressed as how far to slide from the natural break (permissive,
        # "anything on this side of the gap") toward the labelled examples' own similarity
        # (strict, "only things as alike as the ones I picked"). Without this the slider is
        # inert in the mode a session spends most of its time in.
        return float(cut + min(self.q / 0.4, 1.0) * (floor - cut))

    def fit(self, x, y, seed=0, pool=None):
        y = np.asarray(y, dtype=object)
        raw = self._transform(x)
        xs = self._standardize(x, fit=True)
        self.classes = sorted(set(y.tolist()))
        self.dets, self.thr, self.oof_pos = {}, {}, {}
        self.centroid_, self.radius_, self.n_examples_ = {}, {}, {}
        # typical spread of the whole labelled set — the floor for a class with too few
        # examples to have a meaningful radius of its own
        typical = float(np.median(np.linalg.norm(xs - xs.mean(axis=0), axis=1))) or 1.0
        for c in self.classes:
            members = xs[y == c]
            if len(members) == 0:
                continue
            centre = members.mean(axis=0)
            d = np.linalg.norm(members - centre, axis=1)
            spread = float(np.quantile(d, 0.9)) if len(d) > 2 else float(d.max() if d.size else 0)
            self.centroid_[c] = centre
            self.radius_[c] = max(spread * (self.ood_scale or 1.0), typical * 0.35)
            self.n_examples_[c] = len(members)
        for c in self.classes:
            yc = (y == c).astype(int)
            if yc.sum() == 0 or yc.sum() == len(yc):
                # No negatives to discriminate against — one-vs-REST needs a rest. This is the
                # state every session starts in: one class labelled, "find me more like these".
                # Fall back to similarity, which is a question a single class CAN answer.
                #
                # Crucially this uses the RAW embedding space, not the standardized one. With a
                # single class the standardizer's mean IS that class's centroid, so every member
                # collapses to the origin and cosine against it measures nothing — measured, the
                # target class scored 0.49 while the classes it should exclude scored 0.71.
                # Embeddings are unit-length by construction; cosine is their native metric.
                proto = raw[yc == 1].mean(axis=0)
                self.dets[c] = ("proto", proto)
                sims = _proto_score(raw[yc == 1], proto)
                self.oof_pos[c] = sims
                self.thr[c] = self._one_class_threshold(sims, raw, proto, pool)
                continue
            pos = self._oof_positive_scores(xs, yc, seed)
            self.oof_pos[c] = pos
            # Two things set the bar, and the higher wins:
            #   the out-of-fold quantile — where this class's own held-out members actually score
            #   a floor that RISES with strictness — so the control always has traction
            # Without the second the slider was inert with more than one class: measured on 817
            # real polygons, thresholds sat pinned at min_confidence across the whole range and
            # the unknown count did not move at all (27 -> 27 from q=0 to q=0.40). The quantile
            # alone cannot clear the floor, because logistic scores on a handful of examples are
            # not high enough for a low quantile to bind.
            self.thr[c] = max(float(np.quantile(pos, self.q)),
                              self.min_confidence, self._strict_floor())
            self.dets[c] = ("linear",) + _fit_logistic(xs, yc, self.C)
        return self

    @property
    def single_class(self):
        """True when there is nothing to discriminate against, so scores are similarities
        rather than probabilities and should be read as a ranking."""
        return len(self.classes) == 1

    # ---------- prediction ----------
    def scores(self, x):
        raw = self._transform(x)
        xs = (raw - self.mean_) / self.std_
        rows = []
        for c in self.classes:
            det = self.dets[c]
            if det[0] == "proto":
                rows.append(_proto_score(raw, det[1]))     # raw space — see fit()
            else:
                rows.append(_sigmoid(xs @ det[2] + det[1]))
        return np.vstack(rows)                                            # (C, N)

    def _near(self, x):
        """[C, N] bool — is each object inside the radius of each class's examples?

        Not used in single-class mode: there the similarity threshold already IS the
        familiarity test, and a radius calibrated on a handful of hand-picked examples is far
        tighter than the class really is (measured: it passed 3 of 75 objects).
        """
        if not self.ood_scale or not self.centroid_ or self.single_class:
            return None
        xs = (self._transform(x) - self.mean_) / self.std_
        rows = []
        for c in self.classes:
            if self.n_examples_.get(c, 0) < self.min_for_gate:
                rows.append(np.ones(len(xs), bool))       # too few examples to judge distance
            else:
                rows.append(np.linalg.norm(xs - self.centroid_[c], axis=1) <= self.radius_[c])
        return np.vstack(rows)

    def predict(self, x):
        """(labels, scores). UNKNOWN means no detector was both confident AND familiar."""
        s = self.scores(x)
        near = self._near(x)
        if self.decision == "argmax":
            # Best guess means BEST GUESS: the highest-scoring class, always. It used to also
            # require clearing a 0.5 floor AND passing the distance gate, so a control offering
            # "prefer a best guess over unknown" still returned unknown — which is not a
            # subtlety, it is the label being untrue. Anyone who wants abstention has the other
            # mode; this one exists precisely to force a decision.
            return (np.array([self.classes[int(k)] for k in s.argmax(0)], dtype=object), s)
        fires = s >= np.array([self.thr[c] for c in self.classes])[:, None]
        if near is not None:
            fires &= near
        out = []
        for j in range(s.shape[1]):
            if not fires[:, j].any():
                out.append(UNKNOWN)
            else:
                out.append(self.classes[int(np.argmax(np.where(fires[:, j], s[:, j], -1.0)))])
        return np.array(out, dtype=object), s

    def confidence(self, x):
        return self.scores(x).max(axis=0)

    def suspect_labels(self, y):
        """Training examples whose own out-of-fold score fell below their class's bar —
        candidates for a mislabel or a genuinely ambiguous object."""
        y = np.asarray(y, dtype=object)
        out = {}
        for c in self.classes:
            pos = np.where(y == c)[0]
            scores = self.oof_pos.get(c, np.array([]))
            out[c] = sorted(((int(p), float(s)) for p, s in zip(pos, scores) if s < self.thr[c]),
                            key=lambda t: t[1])
        return out


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))


def _otsu(hist):
    """The split of a histogram that best separates it into two groups, as a 0..1 position.
    Same method the change map's Auto button uses, applied to similarity instead of change."""
    hist = np.asarray(hist, np.float64)
    total = hist.sum()
    if total <= 0:
        return 0.5
    centers = (np.arange(len(hist)) + 0.5) / len(hist)
    w0 = np.cumsum(hist)
    w1 = total - w0
    csum = np.cumsum(hist * centers)
    with np.errstate(invalid="ignore", divide="ignore"):
        between = w0 * w1 * (csum / w0 - (csum[-1] - csum) / w1) ** 2
    between[~np.isfinite(between)] = -np.inf
    return float(centers[int(np.argmax(between))]) if np.isfinite(between).any() else 0.5


def _proto_score(xs, centroid):
    """Cosine similarity to a class prototype, mapped to 0..1 so it can stand in for a
    detector probability everywhere else in this class."""
    xs = np.atleast_2d(xs)
    nx = np.linalg.norm(xs, axis=1)
    nc = np.linalg.norm(centroid)
    cos = (xs @ centroid) / np.maximum(nx * nc, 1e-12)
    return (1.0 + cos) * 0.5


def review_order(pred, scores, locked=None):
    """The human's work-list: the objects whose answers are least trustworthy, worst first.

    Abstentions come first (the head had no opinion at all), then the smallest margin between
    the best and second-best class — the ones it nearly called differently. Ported from the
    prototype's `review_queue`, which is where the labelling effort actually pays off: correcting
    a confident-and-wrong object teaches less than resolving one the head is torn about.
    """
    n = scores.shape[1]
    locked = np.zeros(n, bool) if locked is None else np.asarray(locked, bool)
    free = np.flatnonzero(~locked)
    if free.size == 0:
        return np.array([], int)
    s = np.sort(scores[:, free], axis=0)
    margin = (s[-1] - s[-2]) if s.shape[0] > 1 else -s[-1]   # one class: least similar first
    is_unknown = np.asarray(pred, dtype=object)[free] == UNKNOWN
    return free[np.lexsort((margin, ~is_unknown))]


# ---------- presets ----------

def save_classes(path, classes, colors=None):
    """A preset stores the LABELLED EXAMPLE VECTORS, not fitted coefficients.

    That way loading refits in under a second, a preset stays valid if this implementation
    changes, and the user's labelling effort — the expensive part — is what actually travels.
    """
    payload = {"version": 1,
               "classes": [{"name": name,
                            "color": (colors or {}).get(name),
                            "vectors": np.asarray(v, np.float32).tolist()}
                           for name, v in classes.items()]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def load_classes(path):
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    classes, colors = {}, {}
    for entry in payload["classes"]:
        classes[entry["name"]] = np.asarray(entry["vectors"], np.float32)
        if entry.get("color"):
            colors[entry["name"]] = entry["color"]
    return classes, colors


def fit_from_classes(classes, pool=None, **kw):
    """Build a head from {class_name: [vectors]}. Returns None if there is nothing to fit.

    `pool` is every candidate object, labelled or not. It is only consulted when a single class
    has been labelled, where the cut in a similarity ranking cannot be found from the labelled
    examples alone — see `_one_class_threshold`.
    """
    names = [c for c, v in classes.items() if len(v)]
    if not names:
        return None
    x = np.concatenate([np.asarray(classes[c], np.float32) for c in names])
    y = np.concatenate([[c] * len(classes[c]) for c in names])
    return OvRHead(**kw).fit(x, y, pool=pool)
