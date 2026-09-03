# How it works

Two stages: find where the surface changed, then tell it what the changes are.

For the citations and the reasoning behind each choice, see [Reference](reference.md).

## What an embedding is

Google and Google DeepMind publish an **embedding** for every 10 m pixel on Earth: 64 numbers
summarising a whole year of satellite observation — optical, radar, elevation — for every year
from 2017 to 2025 ([AlphaEarth Foundations][aef]).

EMBED-CD compares embeddings year-to-year to obtain general changes across a landscape. Changes 
often represent deviations from 'normal' patterns for a particular area.  

## The change score

Change is calculated as the **dot product** between a pixel's two years, remapped so 0 means identical and 1
means opposite. This is the AlphaEarth paper's own change-detection measure, not an adaptation
of it — [equation 8][aef], supplementary section S4.1:

```text
d = (1 - dot(e, p)) / 2
```

- `e`, `p` — the pixel's 64-number embeddings before and after, each L2-normalised to unit
  length.
- `dot(e, p)` — their dot product: `+1` if the two years are identical, `−1` if opposite.
- `d` — the change score: `0` the embeddings were the same, `1` they were on opposite poles.

The scale is **absolute**, never stretched to fit the scene, so a cutoff of 0.15 means the same
thing in every tile and between runs.

!!! note "The useful range is narrow"
    Measured on a real Vancouver Island run, 99% of pixels score below 0.11. Most of the 0–1
    range never appears — two embeddings that far apart would be as unrelated as two random
    points on Earth.

## Data coverage

A companion layer says *why* a pixel has no answer: no tile here, or one of the two years
missing.

Without it, "nothing changed" and "we couldn't tell" both render as blank, and you cannot tell
them apart. That distinction is the difference between a map you can act on and one you can't.

The dataset is global over land, but not everywhere and not equally in every year:

![World map of AlphaEarth tile footprints. Land and shallow water are covered; open ocean and
everything beyond 83.36 degrees north or south is not. Areas added between 2017 and 2025 are
highlighted.](assets/aef-coverage.png){ width="100%" }

Nothing exists beyond **±83.36°**, and nothing over open ocean — coverage is land, shallow
water, reefs and inland waterways. Coverage also **grew** over the record: 33,148 tiles in 2017
against 34,155 in 2025, mostly interior Antarctica and small islands. Pair an early year with a
recent one and you lose the difference, which is precisely what the coverage layer reports back
to you.

## Detail and resolution {#detail-and-cost}

**Detail** sets the output pixel size — 10, 20, 50 or 100 m. Above 10 m the plugin reads the
dataset's own reduced-resolution copies, so a large area takes minutes instead of hours.

Detail means **ground** metres, so the map is written in a CRS where a metre really is a metre:
your project's if it qualifies, otherwise the area's UTM zone.

Before you generate a change map, the panel tells you the tile count, download size, rough time and output
size for the area you have drawn. Note your personal computer limitations before committing to large jobs!

## Thresholds and objects {#thresholds-and-objects}

You can move the cutoff slider at any time and the change map redraws instantly.
Once you have settled on one, Generate Embedded Vector Set turns everything at or above it into 
objects: connected groups of pixels. Each object is then given its own embedding, pulled from a 
hidden grid of 160 m cells lying beneath the raster. That grid never moves, so whichever cutoff 
you pick, every object is described by exactly the cells it covers — weighted by how much 
usable data each cell holds.

![Two stacked layers: the objects a cutoff produced, above a fixed grid of 160 m embedding
cells. Each object takes the cells it lands on.](assets/objects-and-grid.png){ width="100%" }

## Classifying {#classifying}

The embeddings are appended to each object we polygonized in the previous step - allowing for a lightweight 
classification regime. 

Label a few objects by clicking them and the rest are classified as you go.

Each class gets **its own detector**, fitted by [logistic regression][lr] on the objects you
labelled. One detector per class rather than a
single model choosing between them ([one-vs-rest][ovr]) means a class can fire on nothing it
recognises, and adding a class leaves the others untouched. Refitting takes well under a
second, so the map recolours as fast as you can click.

**The first class is a special case.** One-vs-rest needs a rest, and with a single class there
is nothing to discriminate against. So the head answers the question one class *can* answer —
how much does this object look like the ones you labelled? — by ranking every object on
**cosine similarity** to the average of your examples. Read those numbers as an ordering, "most
like this first", not as probabilities. Label a second class and every detector switches to
logistic regression.

Your classes will never cover a whole landscape, and a classifier forced to pick one would file
genuinely new things under whatever they resemble most. So this one is allowed to answer
**unknown**. An object gets a class only if it passes two tests — the detector is confident
enough, *and* the object sits close to the examples that taught that class — so even a very
confident detector can be turned down for landing too far from its own examples. This is the
[open-set][open] problem: your class list is not the world.

Two modes decide what a class **means**:

| Mode | A class is… | Use it when |
|---|---|---|
| **End state** (default) | what it is now: *clearing* | you want classifications of what the change *IS* now
| **Transition** | a change: *forest → clearing* | you care *how* something changed

Transition uses the before-state alongside the change (before and after embeddings) — the features are `[A, B−A]`, the configuration Burns (2026)
calls **"baseline + delta"** and found produced the most spatially coherent maps of the five compared on these embeddings ([Google Earth blog][burns]). End state uses only the after-embeddings to fit the classifier. 

Note that in my testing, its been found that the transition performs well for simple cases such as separating cutblocks from greenup inside of cutblocks. It has not been tested extensively however and will be evaluated more moving forward. 

## Saving and areas {#saving-and-areas}

Set **Save to:** and the run folder *is* the project: tiles, the change map, the embedding
cells, the objects and your labels all live there together. **Open…** brings a run back live —
threshold, polygons and classifier — not just as a picture.

Several areas can be open at once. Each gets its own layer group, and switching between them
restores that area's own objects and labels.

[aef]: https://arxiv.org/abs/2507.22291
[lr]: https://en.wikipedia.org/wiki/Logistic_regression
[ovr]: https://www.jmlr.org/papers/v5/rifkin04a.html
[open]: https://doi.org/10.1109/TPAMI.2012.256
[burns]: https://medium.com/google-earth/rethinking-change-detection-and-attribution-how-you-compare-satellite-embeddings-matters-858f17f577d7
