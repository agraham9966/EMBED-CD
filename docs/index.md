# EMBED-CD

Year-over-year land change maps from satellite embeddings, inside QGIS. Draw a rectangle, pick
two years, press one button — then teach it what the changes *are* by clicking a few of them.

Nothing to `pip install`, no account, no API key, and no model to train.

---

## What makes it different

Most change detection compares what the surface **looked like** on two dates. This compares
what it **did** across two years.

Every 10 m pixel on Earth carries an [AlphaEarth][aef] embedding: 64 numbers summarising a
whole year of satellite observation, published by Google and Google DeepMind for every year
from 2017 to 2025. The change score is the cosine distance between a pixel's two years — so it
responds to a change in *behaviour*, not just a change in colour on one cloud-free day.

Three consequences follow, and they are most of why this tool is shaped the way it is:

**The scale is absolute.** The score is never percentile-stretched, so a cutoff of 0.15 means
the same thing in every tile, in every scene, and between runs. Mosaics have no seams and no
per-tile rescaling.

**"No data" is an answer, not a blank.** A separate coverage layer says *why* a pixel has no
result — no tile, or a year missing. A gap is never rendered as "nothing changed", which is the
failure that makes a change map untrustworthy.

**The embeddings are kept.** As each tile passes through memory it is pooled into 160 m cells
and written beside the change raster. That is what lets you cut the map into objects afterwards
at any threshold, give every object the embedding of what it covers, and train a classifier on
a handful of clicks.

## What it is honest about

The classifier is allowed to answer **unknown**, and that matters: your classes will never
cover a landscape exhaustively, and one that must choose will file genuinely new things under
whatever they resemble most.

The [Limitations](limitations.md) page is a real one. Annual embeddings mean no event timing.
Coarse Detail reads slightly conservative near the cutoff. The reference imagery is composites,
not acquisitions. Every claim there carries the measurement behind it.

## Start here

- **[Install](getting-started/install.md)** — one zip, no pip step.
- **[Quickstart](getting-started/quickstart.md)** — a first change map.
- **[How it works](how-it-works.md)** — the method, in order.

[aef]: https://arxiv.org/abs/2507.22291
