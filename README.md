# EMBED-CD

Map how the land changed between two years, right inside QGIS. Draw a box, pick the years, and
EMBED-CD makes a change map. Click a few of the changes and it labels the rest.

![EMBED-CD in QGIS: a 2019→2024 change map over Vancouver Island, objects classified
(cutblocks in orange) with the panel showing the per-class breakdown.](docs/assets/map_example_vanisle.png)

**Full guide, videos and worked examples: [agraham9966.github.io/EMBED-CD](https://agraham9966.github.io/EMBED-CD/)**

It's for people who work in QGIS — foresters, land managers, remote-sensing analysts — who want
a quick, honest change map without training a model, writing code, or downloading raw imagery.

## What it does

- Makes a year-over-year change map for any area you draw, any two years from 2017 to 2025.
- Shows where the data couldn't answer — no tile, or a missing year — instead of hiding it as
  "no change".
- Turns the changes into objects you label by clicking, then classifies the rest as you go, and
  says "unknown" when it isn't sure.
- Works tile by tile, so large areas stay practical.

## What it doesn't

- Pin down *when* in the year a change happened — it's annual, so it tells you the change
  occurred, not the day.
- Prove a change is real. A drought or an odd season can move the score too; the labelling step
  is where you judge that.
- Need an account, an API key, a pip install, or a GPU.

## Data and requirements

Runs on QGIS 3.28+ (tested on 3.44 and 4.0). It reads Google's
[AlphaEarth](https://arxiv.org/abs/2507.22291) satellite embeddings — a yearly, 10 m, global
summary of the land surface, free under CC BY 4.0 — straight from the cloud. Everything else it
needs ships with QGIS. Tested on Windows; Linux support is coming.

## Install

Add EMBED-CD's plugin repository, so it installs and updates from inside QGIS. In
**Plugins → Manage and Install Plugins → Settings**, tick *Show also experimental plugins*, then
add this under *Plugin Repositories*:

```
https://agraham9966.github.io/EMBED-CD/plugins.xml
```

Find EMBED-CD under *All* and install it. (Coming soon to the official QGIS plugin repository,
where you'll just search for it.)

## Using it

Draw an area, pick two years, and press **Make change map**. Move the cutoff to taste, turn the
result into objects, then click a few to label them and the rest follow. The
[Quickstart](https://agraham9966.github.io/EMBED-CD/getting-started/quickstart/) walks through it
with videos.

EMBED-CD is GPL-2.0-or-later. The Sentinel-2 reference imagery is non-commercial — see the
[docs](https://agraham9966.github.io/EMBED-CD/reference/) before shipping it in a commercial
product.
