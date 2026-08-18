# EMBED-CD

A QGIS plugin that makes year-over-year land change maps from satellite embeddings, and lets
you classify what changed by clicking a few examples.

Draw a rectangle, pick two years, press one button. There is nothing to install beyond the
plugin itself, no account, no API key, and no model to train.

## How it works, briefly

Every 10 m pixel on Earth carries an [AlphaEarth](https://arxiv.org/abs/2507.22291) embedding —
a 64-number summary of a whole year of satellite observation, published by Google and Google
DeepMind for every year from 2017 to 2025. Change is the cosine distance between a pixel's two
years, so it catches changes in *behaviour* over a year, not just changes in colour on one date.

Three things follow from that, and they are the reason this exists:

- **The scale is absolute.** The score is never percentile-stretched, so a cutoff means the
  same thing in every tile and between runs. Mosaics have no seams and no per-tile rescaling.
- **"No data" is its own answer.** A separate coverage layer says *why* a pixel has no result
  (no tile, or a year missing). A gap is never rendered as "nothing changed".
- **The embeddings are kept.** While each tile is briefly in memory it is pooled into 160 m
  cells and written beside the tile. That is what lets you cut the map into objects afterwards,
  at any threshold, and give every object the embedding of what it covers — which is what the
  classifier learns from.

Label a handful of objects and a one-vs-rest head fits in under a second and colours the rest.
It is allowed to answer **unknown**, which matters: your classes will never cover a landscape
exhaustively, and a classifier that must choose will file genuinely new things under whatever
they resemble most.

## Install

1. Download `embed_cd_qgis-<version>.zip` from
   [Releases](https://github.com/agraham9966/EMBED-CD/releases).
2. QGIS → **Plugins → Manage and Install Plugins → Install from ZIP**.
3. A toolbar button and a **Raster → EMBED-CD** menu entry appear.

No `pip install` step. Everything the plugin needs — GDAL, numpy, scipy, pyarrow — already
ships with QGIS. Requires QGIS 3.28 or newer; developed and tested on 4.0.1.

The first run downloads a one-time 78 MB tile index (cached, ~10 MB afterwards). Data is read
directly from public cloud-optimized GeoTIFFs, so nothing else is stored.

## Using it

1. **Draw an area**, name it, pick two years and a Detail.
2. **Make change map.** Tiles fill the canvas as they land; memory stays flat (~0.6 GB) however
   large the area is. Set *Save to:* and the run is resumable and reopenable.
3. **Move the cutoff**, or press Auto for an Otsu split of the whole mosaic. This is pure
   symbology — the raster holds the continuous score, so it is instant and reversible.
4. **Generate Embedded Vector Set** to cut the changed area into objects carrying embeddings.
5. **Add a class, click a few objects.** Everything else is classified as you go. Use the
   arrows to step through whatever the model is least sure about.

**Detail** is what makes large areas possible. Above 10 m the plugin reads the data's own
built-in reduced-resolution copies, so a tile covers proportionally more ground for the same
bytes: all of Vancouver Island at 100 m takes about three minutes. The 160 m embedding cells
behind the classifier are identical either way, so a coarse run still gives you objects and
classes.

## Development

```bash
python scripts/make_release.py        # -> releases/embed_cd_qgis-<version>.zip
```

The zip is self-contained: `scripts/make_release.py` vendors the `embed_cd/` engine and the
logo inside the plugin folder, so an installed copy needs nothing from this repo.

Tests need QGIS's own Python, because the engine uses `osgeo.gdal`:

```bash
"C:\Program Files\QGIS 4.0.1\bin\python-qgis.bat" run_tests.py
```

To work on it live, symlink the plugin folder into your QGIS profile instead of installing a
zip — the plugin detects that layout and imports the engine from the repo root.

## Layout

- `embed_cd/` — the engine. Pure numpy/scipy/GDAL, imports no QGIS, runs and tests standalone.
- `plugin/embed_cd_qgis/` — the QGIS plugin: the dock, the classifier panel, the map tool.
- `tests/` — run them with `run_tests.py`.

The change job runs in a subprocess, because PROJ and GDAL are not safe on QGIS's own threads
and a long job must never block the UI.

## Data, licences and limits

- **AlphaEarth Foundations Satellite Embedding V1** — Google and Google DeepMind,
  **CC-BY 4.0**. Global, every year 2017–2025, read from public COGs on source.coop.
- **Sentinel-2 cloudless** year photos — [EOX IT Services](https://s2maps.eu), contains
  modified Copernicus Sentinel data. **CC BY-NC-SA 4.0 — non-commercial** for 2018 onward
  (2016 is CC BY 4.0). If your output is commercial, do not ship these tiles in it.
- This plugin is **AGPL-3.0**.

Known limits, plainly:

- **Annual only.** One embedding per calendar year, so there is no sub-annual or event-timed
  detection. A 2019→2024 map includes changes during 2024, but a November change is diluted by
  ten months of pre-change observation in the same embedding.
- **Coarse Detail reads slightly conservative** near the cutoff (measured: 7.6% of the area
  flagged versus 9.2% at cutoff 0.15, correlation 0.983).
- **The year photos are composites**, not acquisitions. They answer "what was here that year",
  never "what date did this change".
