"""Emit examples/embed_cd_demo.ipynb. Built as a dict so nothing needs hand-escaping."""
import io
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": text.rstrip("\n").splitlines(keepends=True)}


cells = [
    md("""# EMBED-CD from plain Python

The QGIS plugin is a shell around the `embed_cd` package. Nothing in that package imports QGIS
or Qt, so the whole pipeline runs from a script or a notebook.

**Which Python to run this with**

- Parts 1 and 2 need only **numpy + scipy** — any environment.
- Parts 3 and 4 also need **GDAL** (`osgeo`) and network access.

**QGIS's own interpreter already has all of it** — numpy, scipy, GDAL and pyarrow — which makes
it the least-effort choice. Add JupyterLab to it once:

```
# Windows
"C:\\Program Files\\QGIS <version>\\bin\\python-qgis.bat" -m pip install --user jupyterlab
"C:\\Program Files\\QGIS <version>\\bin\\python-qgis.bat" -m jupyterlab

# Linux / macOS
python3 -m pip install --user jupyterlab
python3 -m jupyterlab
```

> **A conda environment may not work.** Measured on one Windows machine: conda-forge's scipy
> crashed the interpreter inside L-BFGS-B on the *second* iteration (`0xC06D007F`, DLL not
> found), which kills the classifier in Part 2. It reproduces with plain scipy and no EMBED-CD
> involved — `maxiter=1` succeeds, `maxiter=2` dies. If you hit it, use QGIS's interpreter or a
> pip-built scipy.
"""),

    code('''import os
import sys

# Point at the repo checkout (unnecessary if embed_cd is already on the path).
REPO = os.path.abspath("..")
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import numpy as np
import embed_cd

print("embed_cd modules:", ", ".join(embed_cd.__all__))

# GDAL is imported lazily, so check here whether Parts 3-4 can run.
try:
    from osgeo import gdal
    HAVE_GDAL = True
    print("GDAL:", gdal.__version__, "-> Parts 3 and 4 will run")
except ImportError:
    HAVE_GDAL = False
    print("no GDAL -> Parts 1 and 2 only")'''),

    md("""## Part 1 — The change score (numpy only)

`score.change_score` is the core measure: L2-normalise each pixel's two embedding vectors, take
the dot product, and remap so `0` = identical and `1` = opposite. That is the AlphaEarth paper's
own equation 8.

It does **not** care where the vectors came from — any two `[H, W, D]` cubes work, so this is
usable on your own embeddings.
"""),

    code('''from embed_cd import score as S

rng = np.random.default_rng(0)
before = rng.normal(size=(64, 64, 64)).astype(np.float32)
after = before.copy()
after[40:, 40:] = rng.normal(size=(24, 24, 64))      # a patch that "changed"

chg, cov = S.change_score(before, after)
print("change map:", chg.shape, "range %.3f - %.3f" % (chg.min(), chg.max()))

# Coverage is a companion band saying WHY a pixel has no answer -- never a silent 0.
print("coverage codes:", {int(v): S.COV_LABELS[int(v)] for v in np.unique(cov)})

# The histogram is fixed-range, which is what lets per-tile histograms simply be summed and a
# cutoff be chosen for a whole mosaic without ever holding the mosaic in memory.
hist = S.histogram(chg)
for cut in (0.05, 0.10, 0.20):
    print("cutoff %.2f -> %5.1f%% of pixels flagged" % (cut, 100 * S.fraction_above(hist, cut)))

# S.otsu_from_histogram(hist) picks one automatically (the plugin's "Auto" button). It wants a
# real scene: on synthetic data like this the histogram is a spike at zero and Otsu lands in the
# very first bin, which is not informative.'''),

    md("""## Part 2 — The classifier (numpy + scipy)

`head.OvRHead` is one logistic-regression detector per class, each allowed to answer
**unknown**. It takes stored `[A, B]` pairs (before and after embeddings concatenated) and
transforms them according to `features`:

| `features` | detectors see | meaning |
|---|---|---|
| `"delta"` (default) | `[A, B-A]` | the transition: what it was, and what happened to it |
| `"after"` | `[B]` | the end state: what it is now |
| `"raw"` | `[A, B]` | both absolute states |

Also independent of AlphaEarth — any `[A, B]` feature vectors work.
"""),

    code('''from embed_cd import head as H

D = 64
rng = np.random.default_rng(1)

def block(a_centre, b_centre, n):
    """n objects that start near a_centre and end near b_centre."""
    a = np.tile(a_centre, (n, 1)) + rng.normal(scale=0.15, size=(n, D))
    b = np.tile(b_centre, (n, 1)) + rng.normal(scale=0.15, size=(n, D))
    return np.concatenate([a, b], axis=1).astype(np.float32)

forest = rng.normal(size=D)
cleared = rng.normal(size=D)
water = rng.normal(size=D)

classes = {
    "forest -> clearing": block(forest, cleared, 12),
    "forest -> water": block(forest, water, 12),
}

clf = H.fit_from_classes(classes)          # same as OvRHead().fit(x, y)
print("classes:", clf.classes)
print("features:", clf.features)

labels, scores = clf.predict(block(forest, cleared, 5))
print("held-out 'forest -> clearing' predicted as:", labels.tolist())'''),

    code('''# Something unlike anything it was trained on must come back UNKNOWN rather than being forced
# into the nearest class. Two tests: confident enough, AND near the examples that defined it.
alien = np.concatenate([rng.normal(size=(5, D)) * 6,
                        rng.normal(size=(5, D)) * 6], axis=1).astype(np.float32)
labels, _ = clf.predict(alien)
print("alien objects predicted as:", labels.tolist())

# The review queue: least trustworthy answers first, for deciding what to label next.
x_pool = np.concatenate([block(forest, cleared, 6), alien])
pred, sc = clf.predict(x_pool)
print("review order (worst first):", H.review_order(pred, sc)[:6].tolist())'''),

    md("""## Part 3 — A real change map

`job.run` is the whole pipeline: list the AlphaEarth tiles covering a bbox, read two years of
each, score it, reproject only the 1-band result into one output grid, write a GeoTIFF per tile.
Memory stays at one tile pair no matter how large the area is.

**Needs GDAL and network.** The first call downloads a ~4 MB tile index once, then caches it.

`dst_crs` must be a CRS whose units are metres, because `res_m` is in ground metres — use the
local UTM zone, never EPSG:4326.
"""),

    code('''if not HAVE_GDAL:
    print("skipped: needs GDAL")
else:
    import tempfile
    from embed_cd import job

    OUT = tempfile.mkdtemp(prefix="embedcd_demo_")
    BBOX = (-125.30, 49.65, -125.25, 49.70)      # a few km on Vancouver Island

    def on_tile(done, total, rec, hist_total):
        print("  tile %d/%d" % (done, total))

    grid, tiles, hist, partial = job.run(
        bbox=BBOX, year_a=2019, year_b=2024, out_dir=OUT,
        dst_crs="EPSG:32610",      # UTM 10N -- metres
        res_m=10.0,                # output pixel size, in GROUND metres
        cell_m=160.0,              # also pool embeddings, for Part 4
        on_tile=on_tile,
    )
    print("\\ngrid %d x %d px in %s" % (grid.width, grid.height, grid.crs))
    print("%d tile(s) written to %s" % (len(tiles), OUT))
    print("tiles with only one of the two years:", len(partial))'''),

    code('''if HAVE_GDAL:
    from embed_cd import vrt

    # Stitch the per-tile GeoTIFFs into one mosaic. Relative paths, so the folder can be moved.
    VRT = os.path.join(OUT, "change_2019_2024.vrt")
    vrt.write_vrt(VRT, grid, tiles)

    cut = S.otsu_from_histogram(hist)
    print("auto cutoff %.3f -> %.2f%% of the area changed"
          % (cut, 100 * S.fraction_above(hist, cut)))
    print("open in QGIS or rasterio:", VRT)'''),

    md("""## Part 4 — Objects, their embeddings, and classifying them

Threshold the change map into connected objects, give each the mean embedding of the 160 m cells
it covers, then classify. This is what the plugin's *Generate Embedded Vector Set* button does.
"""),

    code('''if HAVE_GDAL:
    from embed_cd import objects as OB

    polys, crs = OB.polygonize(VRT, threshold=cut, min_area_ha=1.0)
    print("%d object(s) at cutoff %.3f" % (len(polys), cut))
    if polys:
        p = polys[0]
        print("  first object: %.1f ha, mean change %.3f, %d px"
              % (p["area_ha"], p["chg_mean"], p["n_px"]))

    # The cell store written during the run holds the pooled embeddings.
    index = OB.CellIndex(OUT, 2019, 2024, cell_m=160.0)
    print("cell store present:", bool(index), "| embedding depth:", index.depth)

    vecs = OB.attach_vectors(polys, index, crs)
    print("object vectors:", vecs.shape, "([A, B] per object)")'''),

    code('''if HAVE_GDAL and len(polys) >= 4:
    # Label a couple by hand (here the two largest vs the two smallest) and let the head do the
    # rest. In the plugin this is the click-to-label loop.
    order = np.argsort([p["area_ha"] for p in polys])[::-1]
    labelled = {
        "big change": vecs[order[:2]],
        "small change": vecs[order[-2:]],
    }
    clf2 = H.fit_from_classes(labelled, pool=vecs)
    pred, sc = clf2.predict(vecs)
    for name in list(clf2.classes) + [H.UNKNOWN]:
        print("  %-14s %3d objects" % (name, int((pred == name).sum())))
else:
    print("skipped: needs GDAL and at least 4 objects")'''),

    md("""## What landed where

The run folder is self-contained, and is what the plugin's **Open…** reads back:

| File | What |
|---|---|
| `tile_*.tif` | per-tile 2-band result: change score + coverage |
| `change_<a>_<b>.vrt` | the mosaic stitching those tiles, relative paths |
| `cells_*.tif` | pooled 160 m embeddings, for classifying objects |
| `objects_<a>_<b>.gpkg` | objects + embeddings, if you call `store.save_objects` |
| `run.json` | the name given to the area |

## Notes

- `res_m` is **ground** metres, so `dst_crs` must be metric. In a degrees-based CRS the output
  grid collapses to a pixel or two and no tile lands in it.
- A coarser `res_m` reads the COGs' built-in overviews, so a large area costs minutes not hours.
- Failures are logged rather than raised. Call `logging.basicConfig(level=logging.INFO)` before
  `job.run` to see why a tile was skipped.
- Data: AlphaEarth Foundations Satellite Embedding V1, Google / Google DeepMind, CC BY 4.0.
"""),
]

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = os.path.join(REPO, "examples", "embed_cd_demo.ipynb")
os.makedirs(os.path.dirname(out), exist_ok=True)
with io.open(out, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
print("wrote", out, "(%d cells)" % len(cells))
