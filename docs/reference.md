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
[Detail and resolution](how-it-works.md#detail-and-cost).

Other entry points: `embed_cd.objects.polygonize` and `attach_vectors` cut objects and give
them embeddings; `embed_cd.head.fit_from_classes` is the classifier; `embed_cd.store` handles
the GeoPackage and label files.

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
- **Open-set recognition** — Scheirer, W.J., Rocha, A., Sapkota, A. & Boult, T.E. (2013).
  *Toward Open Set Recognition.* IEEE Trans. Pattern Analysis and Machine Intelligence
  35(7):1757-1772. [doi:10.1109/TPAMI.2012.256](https://doi.org/10.1109/TPAMI.2012.256)
- **Reject option** — Chow, C.K. (1970). *On optimum recognition error and reject tradeoff.*
  IEEE Trans. Inf. Theory 16(1):41–46. · Fumera, G., Roli, F. & Giacinto, G. (2000). *Reject
  Option with Multiple Thresholds.* Pattern Recognition 33(12):2099–2101.
- **Change magnitude vs change type** — Cohen, W.B. & Fiorella, M. (1998). *Comparison of
  methods for detecting conifer forest change with Thematic Mapper imagery.* In Lunetta, R.S. &
  Elvidge, C.D. (eds), *Remote Sensing Change Detection*, pp. 89-102. Ann Arbor Press. ·
  Kennedy, R.E., Yang, Z. & Cohen, W.B. (2010). *Detecting trends in forest disturbance and recovery using
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

**EMBED-CD itself** — GPL-2.0-or-later.

[ee]: https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL
[burns]: https://medium.com/google-earth/rethinking-change-detection-and-attribution-how-you-compare-satellite-embeddings-matters-858f17f577d7
