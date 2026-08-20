# Quickstart

A first change map, and then a first classified one. Ten minutes on a small area.

The panel is three numbered steps. Each opens as you finish the one before it, and each step's
header keeps the answer you gave it, so a folded step still tells you what it is holding.

---

## 1 · Area, years and output

**Draw area on map**, then drag a rectangle. A line under the button tells you what that area
will cost *before* you commit to it:

```
Area ~12×12 km · 6 tiles · 0.8 GB to read · ~1 min · output 1.4 Mpx
```

Start small — a few kilometres across. The download is the slow part, and it scales with area.

Give it a **Name** if you like; its layers go into a QGIS group of that name, so several areas
can sit in one project without becoming a pile of near-identical entries.

Pick **From** and **To** years (2017–2025), and a **Detail**. Leave Detail at *10 m (full)* for
a small area — see [Detail and cost](../using-it/detail-and-cost.md) for when to change it.

Set **Save to:** if you want to keep the results. Leave it empty and the run is scratch,
discarded when QGIS closes.

Press **Make change map**. Tiles fill the canvas as they land. Memory stays flat at around
0.6 GB however large the area is, and **Cancel** stops after the current tile — finished tiles
are kept, so re-running resumes rather than restarting.

!!! tip "Look at the imagery first"
    The **Reference imagery** strip at the bottom streams Sentinel-2 cloudless mosaics, one
    button per year. It needs no area and no run, so it is useful *before* you decide where to
    draw — and afterwards, for checking which year a change actually appeared in.

## 2 · Change map

You now have two layers: the change map, and a **data coverage** layer beneath it.

The coverage layer is the one that makes the map trustworthy. Anything you can *see* on it is
somewhere the change map has no opinion — a missing year, or no tile at all. If a spot is blank
on both, it was surveyed and did not change.

Move **Changed if ≥** to set the cutoff. This is pure symbology: the raster holds the
continuous score, so the slider is instant, works mid-run, and survives reopening the layer.
**Auto** picks a cutoff for the whole mosaic (Otsu) and reports how much of the area it flags.

That is a complete change map. If that is all you wanted, **Export** has *Plain polygons* and
*Save as GeoTIFF…* and you are done.

## 3 · Objects and classes

**Generate Embedded Vector Set** cuts everything above the cutoff into objects and gives each
one the embedding of what it covers. `min size` drops speckle — 1 ha is a sensible start.

Then:

1. **Add a class** with the `+` button. In Transition mode it asks for *From* and *To*, so the
   class is named for the change — `forest → clearing`. Fill only the first box for a plain
   name.
2. Turn on **Label by clicking the map** and click a few objects. Right-click removes a label.
3. Everything else is classified as you go — one labelled object is enough to start.

The readout under the buttons shows what the classifier makes of the selected object, one bar
per class, with the ones that cleared their own confidence bar in bold. A road cutting through
a cutblock scoring high on *both* is the classes overlapping, not the model failing — and
seeing that is the point.

**Step through** walks the objects worth your attention. *Least certain first* is the work-list:
abstentions, then the ones it nearly called differently, skipping what you have already
answered. Pick a class in the list and the filter offers `Only "cutblock"` to walk just that
one.

Use the **identify** button beside the colour chip to click an object and read its scores
*without* labelling it.

!!! note "Unknown is a real answer"
    An object the classifier will not commit to stays `unknown` rather than being forced into
    the nearest class. That is deliberate — see [Classifying](../using-it/classifying.md).

---

## Next

- [How it works](../how-it-works.md) — what the score actually measures.
- [Worked example](worked-example.md) — a real job, end to end.
- [Limitations](../limitations.md) — what it cannot tell you.
