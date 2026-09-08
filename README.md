# EMBED-CD

A QGIS plugin for mapping year-over-year land change from satellite embeddings. Draw a box, pick
two years, and it makes a change map. Click a few of the changes to teach it what they are, and
it labels the rest.

There is no pip install, no account, and no API key. Nothing to train.

## What it's doing

Google and Google DeepMind publish [AlphaEarth](https://arxiv.org/abs/2507.22291): for every
10 m pixel on Earth, and every year from 2017 to 2025, a list of 64 numbers that summarise a
whole year of satellite data. Two years of the same pixel that look alike got 64 similar numbers;
two years that differ got different ones. The change score is how far apart those two lists are.

Because it summarises a year rather than a single clear day, it picks up changes in how the land
behaved, not just how it looked on one date.

A few things fall out of that, and they are the point of the tool:

- The score is on a fixed scale, never stretched to the scene, so a cutoff of 0.15 means the same
  thing everywhere and between runs. Tiles mosaic together with no seams.
- Where there's no answer, a second layer says why — no tile, or one of the two years missing —
  instead of drawing a gap as "nothing changed".
- The embeddings are kept, pooled into 160 m cells, so you can cut the map into objects at any
  cutoff afterwards and give each object the embedding of what it covers. That's what the
  classifier reads.

The classifier is one small detector per class, fit in under a second from the objects you
labelled. It can answer "unknown" — your classes never cover a whole landscape, and a classifier
forced to pick would file new things under whatever they resemble most.

## Install

The easy way is to add EMBED-CD's own plugin repository, so it installs and updates from inside
QGIS. In **Plugins → Manage and Install Plugins → Settings**, tick *Show also experimental
plugins*, then under *Plugin Repositories* add:

```
https://agraham9966.github.io/EMBED-CD/plugins.xml
```

Then find EMBED-CD under *All* and install it. Or, if you'd rather, download the zip from
[Releases](https://github.com/agraham9966/EMBED-CD/releases) and use *Install from ZIP*.

Either way there's no pip step — GDAL, numpy and scipy all ship with QGIS. It needs QGIS 3.28 or
newer, and is tested on 3.44 and 4.0.1. The first run fetches a small (~4 MB) index of where the
data tiles are, caches it, and reads everything else straight from public cloud GeoTIFFs.

## Using it

1. Draw an area, name it, pick two years and a Detail.
2. Press **Make change map**. Tiles fill in as they download. Set *Save to:* if you want to keep
   the run and reopen it later.
3. Drag the cutoff, or press **Auto**. The raster holds the raw score, so this is just symbology —
   instant and reversible.
4. Press **Generate Embedded Vector Set** to turn the changed area into objects.
5. Add a class and click a few objects. The rest are labelled as you go. The arrows walk you
   through whatever the model is least sure about.

**Detail** is what makes big areas practical. Above 10 m the plugin reads the data's own
lower-resolution copies, so each tile covers more ground for the same download — you can scout a
whole region coarsely, then rerun a smaller area at full resolution. The 160 m cells the
classifier uses are the same either way.

## Development

Build the installable zip (and refresh the hosted repository files under `docs/`):

```bash
python scripts/make_release.py
```

The zip is self-contained: the build copies the `embed_cd/` engine and the logo inside the plugin
folder, so an installed copy needs nothing from this repo.

The tests need QGIS's own Python, because the engine calls `osgeo.gdal`:

```bash
"C:\Program Files\QGIS 4.0.1\bin\python-qgis.bat" run_tests.py
```

To develop against a live QGIS, symlink `plugin/embed_cd_qgis` into your QGIS profile instead of
installing a zip. The plugin notices that layout and imports the engine from the repo.

## Layout

- `embed_cd/` — the engine. Plain numpy/scipy/GDAL, no QGIS, runs and tests on its own.
- `plugin/embed_cd_qgis/` — the QGIS side: the dock, the classifier panel, the map tool.
- `examples/embed_cd_demo.ipynb` — the whole pipeline in a notebook, on real data.
- `tests/` — run with `run_tests.py`.

The change job runs in its own process, so a long download never freezes QGIS and GDAL stays off
the UI thread.

## Data and licences

- **AlphaEarth Foundations Satellite Embedding V1** — Google / Google DeepMind, CC BY 4.0. Global,
  2017–2025, read from public COGs on source.coop.
- **Sentinel-2 cloudless** reference imagery — [EOX IT Services](https://s2maps.eu), containing
  modified Copernicus Sentinel data. CC BY-NC-SA 4.0, non-commercial, for 2018 on (2016 is
  CC BY 4.0). Don't ship these tiles in a commercial product.
- EMBED-CD itself is GPL-2.0-or-later.

## What it can't do

- It's annual. There's one embedding per calendar year, so it can't time a change within the
  year. A change late in the second year is muted, because most of that year still looks like the
  old state — if you suspect a late change, compare against the following year.
- A high score isn't proof the land changed. A drought or an odd season moves the embedding too.
  Deciding which changes are real is what the labelling is for.
- The reference photos are yearly composites, not dated images. They answer "what was here that
  year", not "what day did it change".

There's a fuller writeup, with worked figures, at
[agraham9966.github.io/EMBED-CD](https://agraham9966.github.io/EMBED-CD/).
