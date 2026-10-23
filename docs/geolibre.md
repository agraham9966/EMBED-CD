# EMBED-CD in GeoLibre

EMBED-CD also runs inside [GeoLibre](https://geolibre.app), on both the desktop app and the web
app. It is the same tool as the QGIS plugin: the same change maps, the same objects, the same
classifier, and class sets move between the two.

## Install

In GeoLibre, open **Settings → Manage Plugins**, find **EMBED-CD** and click **Install**. Then
turn it on from **Plugins → Installed → EMBED-CD**. The panel opens on the right.

You can also install a zip by hand: download `embed-cd-<version>.zip` from
[Releases](https://github.com/agraham9966/EMBED-CD/releases), then use **Settings → Manage
Plugins → Settings → Install from file**.

Needs GeoLibre 3.4 or newer. Nothing else to install, and no account or API key.

## Use it

The panel has the same three steps as in QGIS.

1. **Area, years and output.** Click **Draw area** and drag a box on the map. Give it a name if
   you like, pick two years and a Detail, then click **Make change map**. The map fills in tile
   by tile. The progress bar and **Cancel** sit under the steps, so you can watch it from any
   step.
2. **Change map.** Move the cutoff slider, or click **Auto**. Only change above the cutoff shows.
3. **Objects and classes.** Click **Generate Embedded Vector Set** to cut the change map into
   objects. Add a class, turn on **Label by clicking the map**, and click a few objects. The
   classifier labels the rest as you go.

Each run gets its own group in the Layers panel, and earlier runs stay on the map. Pick a run
under **Area** to go back to it, with its labels as you left them.

## Saving your work

Runs live only as long as GeoLibre is open. Nothing is written to your computer unless you ask.

- **Save run...** writes the run to one zip file, wherever you choose. It holds the change map
  and coverage as GeoTIFFs (they open in QGIS as they are), the objects and your labels.
- **Open run...** brings a saved run back, zooms to it, and restores its objects and labels.
- **Class set** saves and loads your classes as JSON, in the same format as the QGIS plugin.
- To export the objects as GeoJSON or another vector format, use the objects layer's menu in the
  Layers panel.

## Checking a change against imagery

- **Before/after lens.** The button under the class list. Move over the map to see the before
  year's imagery in a circle under your cursor. Hold **A** to see the after year. Shift+scroll
  changes its size, and the arrow next to the button sets the years. It brings its own imagery,
  so no basemap needs to be open, and clicks still label objects.
- **Reference imagery.** The row of years under the steps. Each one adds that year's imagery as a
  layer at the bottom of the Layers panel. Turn on two and switch between them to compare.

The imagery is Sentinel-2 cloudless by EOX. It is a yearly cloud-free composite, so it shows
what a place looked like that year, not on a given date. There is no 2017 mosaic, so 2017 shows
2016. The 2018 and later mosaics are free for non-commercial use only (CC BY-NC-SA 4.0).

## Limits

- Everything runs in the browser, so very large areas at full 10 m detail can run out of
  memory. The panel warns you before that happens. A coarser Detail is much faster.
- The data streams from the internet as you run, so you need a connection.
