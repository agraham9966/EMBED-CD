# Install

## Requirements

- **QGIS 3.28 or newer.** Developed and tested on 4.0.1; 3.28+ should work but is untested.
- Nothing else. GDAL, numpy, scipy and pyarrow all ship with QGIS, which is the whole reason
  the plugin dropped `rasterio` — there is no `pip install` step and no OSGeo4W shell.
- No account, no API key, no Earth Engine. The data is read straight from public
  cloud-optimized GeoTIFFs.

## Install from the zip

1. Download `embed_cd_qgis-<version>.zip` from
   [Releases](https://github.com/agraham9966/EMBED-CD/releases).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**, choose the file,
   **Install Plugin**.
3. A toolbar button and a **Raster → EMBED-CD** menu entry appear. Either opens the panel.

To upgrade, install the newer zip over the old one — the engine is vendored inside the plugin
folder, so an upgrade can never leave you running the previous version's engine against the
new UI.

## The first run

The first job downloads the AlphaEarth tile index once: **78 MB**, reduced to a ≈3.6 MB cache
that every later run loads in well under a second and works offline. It lives in your QGIS
profile, under `cache/embed_cd`.

After that, nothing is stored except the tiles of the areas you actually run.

## Where results go

By default a run writes to a temporary folder that is deleted when QGIS closes. Set
**Save to:** and the run becomes permanent, resumable, and reopenable later with **Open…** —
including its polygons, labels and classifier, not just the picture.

See [Saving and areas](../using-it/saving-and-areas.md).

## If something goes wrong

**"Could not start Python to run the job."** The change job runs in a separate process using
QGIS's own interpreter. If you see this, please
[open an issue](https://github.com/agraham9966/EMBED-CD/issues) with your platform and QGIS
version — it means the interpreter was not where the plugin expected it.

**"pyarrow not installed in QGIS's Python."** Rare: pyarrow ships with the OSGeo4W builds but
not with every distribution's QGIS package. It is needed once, to read the published tile
index. The message tells you the exact command.

**A message about your project CRS.** Detail is in ground metres, so the change map is written
in a CRS where a metre is a metre — your project's own if it qualifies, otherwise the area's
UTM zone. QGIS reprojects it for display and there is nothing to do. See
[Detail and cost](../using-it/detail-and-cost.md).
