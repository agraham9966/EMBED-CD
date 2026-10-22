"""Reference outputs of embed_cd/head.py for the GeoLibre port (run with QGIS's Python).

Separate from make_geolibre_fixtures.py because it needs only the object vectors that script
already wrote (geolibre/test/fixtures/synthetic_vectors.bin), not GDAL.
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from embed_cd import head as H  # noqa: E402

OUT = os.path.join(ROOT, "geolibre", "test", "fixtures")
vecs = np.fromfile(os.path.join(OUT, "synthetic_vectors.bin"), np.float32).reshape(-1, 128)

LABELS = {
    "one": {"cutblock": [2, 5, 9]},
    "two": {"cutblock": [2, 3, 5, 6], "road": [10, 11, 12]},
    "three": {"cutblock": [0, 1, 2, 3, 4, 5], "road": [10, 11, 12, 13, 14],
              "fire → regrowth": [20, 21, 22, 23, 24]},
}


def runs():
    out = []
    for name, lab in LABELS.items():
        classes = {c: vecs[rows] for c, rows in lab.items()}
        for features in ("after", "delta"):
            for decision in ("threshold", "argmax"):
                for q in (0.05, 0.30):
                    head = H.fit_from_classes(classes, pool=vecs, abstain_quantile=q,
                                              decision=decision, features=features)
                    pred, scores = head.predict(vecs)
                    out.append({"labels": name, "features": features, "decision": decision, "q": q,
                                "classes": head.classes, "thr": head.thr,
                                "pred": [str(p) for p in pred], "scores": scores.tolist(),
                                "review": H.review_order(pred, scores).tolist()})
    return out


rng = np.random.default_rng(0)
raw = [int(v) for v in rng.bit_generator.random_raw(6)]
shuffles = {}
for n in (1, 2, 5, 10, 37, 300):
    r = np.random.default_rng(0)
    a = np.arange(n)
    r.shuffle(a)
    shuffles[n] = a.tolist()
ys = [["a"] * 6 + ["b"] * 4 + ["a"] * 3, [0, 1] * 9, ["x"] * 2 + ["y"] * 7]
folds = [{"y": [str(v) for v in y], "k": k, "fold": H._stratified_folds(np.asarray([str(v) for v in y]), k).tolist()}
         for y in ys for k in (2, 3, 5)]

scipy_runs = runs()
sys.modules["scipy"] = None                    # the numpy Newton path, as on Linux QGIS
sys.modules["scipy.optimize"] = None
newton_runs = runs()

with open(os.path.join(OUT, "head.json"), "w", encoding="utf-8") as f:
    json.dump({"raw_u64": [str(v) for v in raw], "shuffles": shuffles, "folds": folds,
               "labels": LABELS, "scipy": scipy_runs, "newton": newton_runs}, f)
H.save_classes(os.path.join(OUT, "classes_qgis.json"),
               {c: vecs[rows] for c, rows in LABELS["two"].items()},
               {"cutblock": "#d85a30", "road": "#1d9e75"}, features="delta")
print("runs", len(scipy_runs), "head.json bytes", os.path.getsize(os.path.join(OUT, "head.json")))
