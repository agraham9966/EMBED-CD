# Quickstart

A first change map, and then a first classified one. Ten minutes on a small area.

The panel is three numbered steps. Each opens as you finish the one before it, and each step's
header keeps the answer you gave it, so a folded step still tells you what it is holding.

---

## 1 · Area, years and output

**Draw area on map**, then drag a rectangle. 
<video controls autoplay loop muted playsinline
       style="width: 100%; border-radius: 4px; margin: 1.2em 0;">
  <source src="../../assets/embed-cd_drawROI.mp4" type="video/mp4">
  Your browser cannot play this video —
  <a href="../../assets/embed-cd_drawROI.mp4">download it instead</a>.
</video>

A line under the button tells you what that area
will cost *before* you commit to it:

```
Area ~12×12 km · 6 tiles · 0.8 GB to read · ~1 min · output 1.4 Mpx
```

Give it a **Name** if you like; its layers go into a QGIS group of that name.

Pick **From** and **To** years (2017–2025), and a **Detail**. Leave Detail at *10 m (full)* for
a small area — see [Detail, resolution](../how-it-works.md#detail-and-cost) for when to change it.

Set **Save to:** if you want to keep the results. Leave it empty and the run is scratch,
discarded when QGIS closes.

Press **Make change map**. Tiles fill the canvas as they land. **Cancel** stops after the current tile — finished tiles
are kept, so re-running resumes rather than restarting. 

!!! tip "Look at the imagery first"
    The **Reference imagery** strip at the bottom streams Sentinel-2 cloudless mosaics, one
    button per year. This is useful for checking which year a change actually appeared in!

## 2 · Change map

You now have two layers: the change map, and a **data coverage** layer beneath it.

The coverage layer provides an understanding of areas which may not have had full coverage - thus resulting 
in an empty change map. 

The change map itself can be adjusted on the fly. Move **Changed if ≥** to set the cutoff. 
**Auto** picks a cutoff for the whole mosaic (Otsu). 

<video controls autoplay loop muted playsinline
       style="width: 100%; border-radius: 4px; margin: 1.2em 0;">
  <source src="../../assets/embed-cd-adjust-threshold.mp4" type="video/mp4">
  Your browser cannot play this video —
  <a href="../../assets/embed-cd-adjust-threshold.mp4">download it instead</a>.
</video>

That is a complete change map. If that is all you wanted, **Export** has *Plain polygons* and
*Save as GeoTIFF…* and you are done.

## 3 · Objects and classes

**Generate Embedded Vector Set** cuts everything above your decided threshold into objects and gives each
one the embedding of what it covers. `min size` drops speckle — 1 ha is a sensible start.

<video controls autoplay loop muted playsinline
       style="width: 100%; border-radius: 4px; margin: 1.2em 0;">
  <source src="../../assets/embed-cd_embed.mp4" type="video/mp4">
  Your browser cannot play this video —
  <a href="../../assets/embed-cd_embed.mp4">download it instead</a>.
</video>

Then:

1. **Add a class** with the `+` button. In Transition mode it asks for *From* and *To*, so the
   class is named for the change — `forest → clearing`. Fill only the first box for a plain
   name.
2. Turn on **Label by clicking the map** and click a few objects. Right-click removes a label.
3. Everything else is classified as you go — one labelled object is enough to start.

The readout under the buttons shows what the classifier makes of the selected object, one bar
per class. A road cutting through
a cutblock scoring high on *both* is the classes overlapping, not the model failing — and
seeing that is the point.

![The per-object readout: one bar per class, the ones that cleared their own confidence bar in
bold.](../assets/confidences.png){ width="100%" }

**Step through** moves you object by object. **Least certain** first puts the unknown ones at the top, then the close calls, and skips anything you have already labelled. Pick a class in the list and the filter offers Only "cutblock" to walk just that one.

Use the **Inspect** button beside the colour chip to click an object and read its scores
*without* labelling it.

!!! note "Unknown is a real answer"
    An object the classifier will not commit to stays `unknown` rather than being forced into
    the nearest class. That is deliberate. If desired, tick **Prefer a best guess over 'unknown'** under the ⚙ button to force a class on everything. 

---

## Next

- [How it works](../how-it-works.md) — what the score actually measures.
- [Limitations](../limitations.md) — what it cannot tell you.
