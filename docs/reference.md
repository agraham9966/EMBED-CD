# Reference

## Using the engine without QGIS

`embed_cd/` imports no QGIS. It runs and tests standalone on numpy, scipy and GDAL, which is
what lets the whole thing be developed and verified outside the application.

```python
from embed_cd import job

grid, tiles, hist, partial = job.run(
    bbox=(-125.4, 49.6, -125.2, 49.8),   # lon/lat
    year_a=2019, year_b=2024,
    out_dir="run", dst_crs="EPSG:32610", res_m=10.0,
    cell_m=160.0,                        # pool embeddings for the classifier
)
```

`dst_crs` must be a CRS in which a metre is a metre — see
[Detail and cost](using-it/detail-and-cost.md).

Other entry points: `embed_cd.objects.polygonize` and `attach_vectors` cut objects and give
them embeddings; `embed_cd.head.fit_from_classes` is the classifier; `embed_cd.store` handles
the GeoPackage and label files.

---

## Method lineage

Nothing here is unprecedented, and where a choice follows established practice it is worth
saying which practice.

### Why the dot product for change

The embeddings are **L2-normalised to the unit hypersphere** — the AlphaEarth paper states they
are "constrained to distribute uniformly in S⁶³" — so the dot product *is* the cosine of the
angle between two years. Google's own dataset documentation makes the recommendation explicit:
embeddings from different years "can be used for condition change detection by considering the
dot product or angle between two embedding vectors" ([Earth Engine catalog][ee]).

Conceptually this is Change Vector Analysis carried into a learned feature space rather than a
spectral one: measure the vector between two dates, threshold its magnitude. What the embedding
adds is that each "band" summarises a year of multi-sensor observation rather than one
cloud-free acquisition.

### Why an absolute score, never a percentile stretch

Per-tile normalisation gives every tile its own scale, which produces visible seams and a
threshold that means something different in each tile. Keeping the score absolute is what makes
a single mosaic possible and a cutoff comparable between runs.

### Why baseline **and** delta for the classifier

The classifier's default features are `[A, B−A]` — the before-state stacked with the change.
This is the configuration Burns (2026) calls **"baseline + delta"**, one of five compared for
change attribution on AlphaEarth embeddings in the western Great Lakes ([Google Earth
blog][burns]).

Her finding, and the reason it is our default: aggregate accuracy was similar across most
configurations, but the *maps* were not. Representations preserving baseline context were
spatially coherent. **Dot-product-only** collapsed 64 dimensions into one number and produced
"widespread speckling, scattered false positives" and "a fundamental loss of class
separability". **Delta-only** fragmented in heterogeneous landscapes, because variability
unrelated to any land-cover transition also produces large embedding differences.

Her `baseline + dot` configuration at 65 dimensions performed on par with the 128-d ones, which
suggests baseline context matters more than raw dimensionality.

The underlying principle is older than embeddings: change *magnitude* alone does not
characterise change *type*, because the same magnitude means different things in forest,
agriculture and urban land (Cohen & Fiorella 1998; Kennedy et al. 2010).

### Why one-vs-rest, and why it may answer "unknown"

Per-class binary detectors rather than one softmax, so a class can fire on nothing it
recognises, and adding a class leaves the others untouched. Rifkin & Klautau's *In Defense of
One-Vs-All Classification* is the standard argument that this is as accurate as more elaborate
multiclass schemes given well-regularised binary classifiers.

Abstention is Chow's **reject option**: decline to classify when no class clears its confidence
bar. We use a **per-class** threshold rather than a single global one, following Fumera, Roli &
Giacinto, *Reject Option with Multiple Thresholds*. Thresholds are calibrated **out of fold**,
because in-sample logistic scores saturate near 1 and would otherwise set a bar that correct
held-out predictions cannot clear.

### Why logistic regression

L2-regularised logistic regression with balanced class weights, fitted by L-BFGS on
standardised features, is the canonical **linear probe** — the standard protocol for using a
frozen foundation model's representation. It is also how the AlphaEarth authors evaluate their
own embeddings ("k-nearest neighbors, and linear layers fit to the features"). It refits in
well under a second on a few hundred vectors, which is what lets the map recolour as fast as
you can click.

### Why score on the source's own grid

Each tile is scored in its native UTM projection, and only the one-band result is reprojected.
This follows Google's guidance for the dataset: mosaicking "loses the original UTM projection",
and retaining each tile's own projection is recommended for accuracy in wide-area analysis.

### Why Otsu for the automatic cutoff

Otsu's method picks the threshold maximising between-class variance — the standard
non-parametric choice for splitting a change-magnitude histogram into changed and unchanged.
Ours accumulates a fixed-range histogram across tiles, so the cutoff is chosen for the whole
mosaic without ever holding the whole mosaic in memory.

---

## Citations

- **AlphaEarth Foundations** — Brown, C.F. *et al.* (2025). *AlphaEarth Foundations: An
  embedding field model for accurate and efficient global mapping from sparse label data.*
  [arXiv:2507.22291](https://arxiv.org/abs/2507.22291)
- **The dataset** — [Satellite Embedding V1, Earth Engine Data Catalog][ee]
- **Comparing embeddings for change** — Burns, M. (2026). *Rethinking Change Detection and
  Attribution: How You Compare Satellite Embeddings Matters.* Google Earth blog. [Link][burns]
- **One-vs-all** — Rifkin, R. & Klautau, A. (2004). *In Defense of One-Vs-All Classification.*
  JMLR 5:101–141.
- **Reject option** — Chow, C.K. (1970). *On optimum recognition error and reject tradeoff.*
  IEEE Trans. Inf. Theory 16(1):41–46. · Fumera, G., Roli, F. & Giacinto, G. (2000). *Reject
  Option with Multiple Thresholds.* Pattern Recognition 33(12):2099–2101.
- **Change magnitude vs change type** — Cohen, W.B. & Fiorella, M. (1998). · Kennedy, R.E.,
  Yang, Z. & Cohen, W.B. (2010). *Detecting trends in forest disturbance and recovery using
  yearly Landsat time series: LandTrendr.* Remote Sensing of Environment 114(12).
  [doi:10.1016/j.rse.2010.07.008](https://doi.org/10.1016/j.rse.2010.07.008)
- **Otsu** — Otsu, N. (1979). *A threshold selection method from gray-level histograms.*
  IEEE Trans. Systems, Man, and Cybernetics 9(1):62–66.

---

## Licences and attribution

**AlphaEarth Foundations Satellite Embedding V1** — Google and Google DeepMind, **CC BY 4.0**.
Global, every year 2017–2025, read from public cloud-optimized GeoTIFFs on source.coop.

**Sentinel-2 cloudless** reference imagery — [EOX IT Services](https://s2maps.eu), containing
modified Copernicus Sentinel data. **CC BY-NC-SA 4.0, non-commercial only** for 2018 onward
(2016 is CC BY 4.0). If your deliverable is commercial, do not ship these tiles in it.
Commercial licences: [cloudless.eox.at](https://cloudless.eox.at).

**EMBED-CD itself** — AGPL-3.0.

[ee]: https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL
[burns]: https://medium.com/google-earth/rethinking-change-detection-and-attribution-how-you-compare-satellite-embeddings-matters-858f17f577d7
