# EMBED-CD

Generate annual land change maps from satellite imagery RAPIDLY, at small and large-scale using QGIS. Draw a rectangle, pick
two years, press one button — then teach it what the changes *are* by clicking a few of them.

<video controls autoplay loop muted playsinline
       style="width: 100%; border-radius: 4px; margin: 1.2em 0;">
  <source src="assets/embed-cd-overview.mp4" type="video/mp4">
  Your browser cannot play this video —
  <a href="assets/embed-cd-overview.mp4">download it instead</a>.
</video>

---

## Built on AlphaEarth embeddings | Expanding to others in future

[AlphaEarth Foundations][aef] (Google / Google DeepMind) describes every 10 m pixel on Earth
with 64 numbers summarising a year of optical, radar and elevation data. Global, annual,
2017-2025, CC BY 4.0, [free to read][ee].

Change is the dot product between a pixel's two embeddings, remapped so 0 means identical
and 1 means opposite — [equation 8][aef] of the AlphaEarth paper. Each embedding covers a year, so it catches changes in
behaviour, not just colour on one clear day.

*Author's Note: I plan to add support for other embedding datasets in the future --only if they are deemed suitable for the platform.*

## Features

- **Draw and run.** Draw a rectangle, pick two years, press one button.
- **Any size of area.** Tiles are read and scored one at a time, so a large area costs no more memory to map than a small one. Coarser Detail is what makes a very large one quick.
- **Self-adjust threshold.** The score is continuous, so the cutoff is symbology - instant and reversible.
- **Classify by clicking.** Label a few objects and the rest follow. Model updates its fit in real-time! Fun! 
- **Check against imagery.** Self-validate outputs with Sentinel-2 mosaics, year by year. Be confident in the changes the model is producing. 

## Outputs

<div class="no-head" markdown>

| Output | What it is |
|---|---|
| **Change map** | A raster of continuous change score, 0–1, on an absolute scale. |
| **Data coverage** | A companion layer saying *why* a pixel has no result: no tile, or a year missing. |
| **Change objects** | A GeoPackage of polygons, each carrying its area, change statistics and the embedding of what it covers. |
| **Classes** | Your labels and predictions on those objects, saved with the run and portable to another area. |
| **GeoTIFF export** | The whole mosaic as a single file, for anything downstream. |

</div>

## Start here

- **[Install](getting-started/install.md)** — one zip, no pip step.
- **[Quickstart](getting-started/quickstart.md)** — a first change map.
- **[How it works](how-it-works.md)** — the method, in order.
- **[Limitations](limitations.md)** — what it cannot tell you.
- **[Reference](reference.md)** — the engine without QGIS, and the citations.

[aef]: https://arxiv.org/abs/2507.22291
[ee]: https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL
