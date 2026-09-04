# Install

## Requirements

- **QGIS 3.28 or newer.** Developed and tested on 4.0.1; 3.28+ should work but is untested.
- **Platform: Windows and macOS.** Windows is tested. macOS is expected to work — the code is
  portable — but is unverified. **Linux is a work in progress and currently non-functional:**
  the plugin installs and reads data, but a change map produces no output on the Linux builds
  tested so far. Track it in the repo before relying on it there.
- Nothing else. GDAL, numpy and scipy all ship with QGIS, which is the whole reason the plugin
  dropped `rasterio` — there is no `pip install` step and no OSGeo4W shell.
- No account, no API key, no Earth Engine. The data is read straight from public
  cloud-optimized GeoTIFFs.

## Install from the plugin repository (recommended)

Add EMBED-CD's own repository once and it installs, updates and reinstalls from inside QGIS —
no downloading, no file copying, which matters if the machine you run it on is a remote one.

1. **Plugins → Manage and Install Plugins… → Settings**.
2. Tick **Show also experimental plugins** (EMBED-CD is flagged experimental until 1.0, and
   without this it will not appear).
3. Under *Plugin Repositories*, **Add…** — name it `EMBED-CD`, URL:

    ```
    https://agraham9966.github.io/EMBED-CD/plugins.xml
    ```

4. Go to **All**, search for `EMBED-CD`, and **Install Plugin**.

Later versions then show up under **Upgradeable** on their own.

## Install from the zip

1. Download `embed_cd_qgis-<version>.zip` from
   [Releases](https://github.com/agraham9966/EMBED-CD/releases).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**, choose the file,
   **Install Plugin**.
3. A toolbar button and a **Raster → EMBED-CD** menu entry appear. Either opens the panel.

To upgrade, install the newer zip over the old one.

## The first run

The first job downloads the AlphaEarth tile index once: **78 MB**, reduced to a ≈3.6 MB cache
that every later run loads in well under a second and works offline. It lives in your QGIS
QGIS profile, under `cache/embed_cd`. Reinstalling the plugin keeps it as long as the
profile survives, so you never re-download it.

After that, nothing is stored except the tiles of the areas you actually run.

## Where results go

By default a run writes to a temporary folder that is deleted when QGIS closes. Set
**Save to:** and the run becomes permanent, resumable, and reopenable later with **Open…** —
including its polygons, labels and classifier, not just the picture.

## Common issues

**"needs either pyarrow or a GDAL built with the Parquet driver"** — you should not see this:
the tile index is downloaded pre-built and read with numpy, which every QGIS has. It only
appears if that download is blocked *and* your QGIS also lacks both Parquet readers. If it does,
either unblock `agraham9966.github.io`, or install pyarrow into the Python QGIS uses
(`sudo apt install python3-pyarrow` on Debian/Ubuntu).

**Find any bugs?** Please
[open an issue](https://github.com/agraham9966/EMBED-CD/issues) with your platform and QGIS
version.

