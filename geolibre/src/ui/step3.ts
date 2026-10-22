// Step 3: cut the change map into objects and give each its embedding ("Generate Embedded Vector
// Set" in the QGIS plugin). Runs on the main thread in slices that yield, so the map and panel stay
// usable, with a progress bar and Cancel.
import { mosaicAsync } from "../engine/job";
import { attachVectors, polygonize } from "../engine/objects";
import type { GeoLibreAppAPI } from "../host-api";
import { ObjectsLayer } from "../map/objectslayer";
import { buildClassify } from "./classify";
import { cellsOf } from "../runfile";
import type { SessionRuns } from "../runs";
import { el, row } from "./dom";

export class Cancelled extends Error {}

export const countText = (n: number, cutoff: number) => `${n} objects at cutoff ${cutoff.toFixed(2)}.`;

/** A tick for long loops: gives the browser a turn after every ~50 ms of work; throws Cancelled. */
export function makeTicker(now: () => number, yieldNow: () => Promise<void>, cancelled: () => boolean) {
  let last = now();
  return async () => {
    if (cancelled()) throw new Cancelled();
    if (now() - last > 50) {
      await yieldNow();
      last = now();
      if (cancelled()) throw new Cancelled();
    }
  };
}

type Deps = {
  app: GeoLibreAppAPI; runs: SessionRuns; currentRun: () => string | null; cutoff: () => number;
  /** Objects now exist: they are the answer, so the change raster steps back (QGIS unticks it). */
  onObjectsMade?: () => void;
};

export function buildStep3(deps: Deps): { root: HTMLElement; runChanged(id: string | null): void; dispose(): void } {
  const root = el("section", "ecd-step");
  root.append(el("h3", "ecd-h", "3. Objects and classes"));
  const makeBtn = el("button", "ecd-btn ecd-primary", "Generate Embedded Vector Set") as HTMLButtonElement;
  makeBtn.title = "Cut the change map at the current cutoff and give every object the embedding of what it covers.";
  const minArea = el("input", "ecd-number") as HTMLInputElement;
  Object.assign(minArea, { type: "number", min: "0.01", max: "1000", step: "0.01", value: "1" });
  minArea.title = "Ignore objects smaller than this. Without it a low cutoff produces thousands of specks.";
  const count = el("p", "ecd-muted", "");
  const bar = el("progress", "ecd-progress") as HTMLProgressElement;
  bar.hidden = true;
  const status = el("p", "ecd-status", "");
  const classify = buildClassify(deps.app, (s) => { status.textContent = s; });
  root.append(row(makeBtn), row(el("span", "", "min size"), minArea, el("span", "", "ha")), count, bar, classify.root, status);

  let layer: ObjectsLayer | null = null;
  let cancel = false, busy = false;

  makeBtn.onclick = async () => {
    if (busy) { cancel = true; return; }
    const id = deps.currentRun(), run = id ? deps.runs.get(id) : null;
    if (!run || !run.tiles.length) { status.textContent = "Make a change map first."; return; }
    const cells = cellsOf(run.tiles, run.cells);
    if (!cells.length) {
      status.textContent = "No embeddings were captured for this run. Make the change map again with this version.";
      return;
    }
    const cutoff = deps.cutoff(), min = Math.max(0, Number(minArea.value) || 0);
    busy = true; cancel = false;
    makeBtn.textContent = "Cancel";
    bar.hidden = false;
    bar.removeAttribute("value");
    count.textContent = "";
    status.textContent = "Cutting objects...";
    const tick = makeTicker(() => performance.now(), () => new Promise((r) => setTimeout(r, 0)), () => cancel);
    try {
      const { meta } = run;
      const polys = await polygonize(meta.grid, (await mosaicAsync(meta.grid, run.tiles, tick)).score, cutoff, min, tick);
      if (!polys.length) {
        status.textContent = "Nothing above the current cutoff. Lower it and retry.";
        return;
      }
      status.textContent = `Reading embeddings for ${polys.length} objects...`;
      const vectors = await attachVectors(polys, meta.grid.crs, cells, null, async (done, total) => {
        bar.max = total; bar.value = done;
        await tick();
        return true;
      });
      deps.runs.setObjects(meta.id, { polys, vectors, cutoff, minArea: min });
      show(meta.id);
      deps.onObjectsMade?.();
      count.textContent = `${countText(polys.length, cutoff)} Add a class, turn on 'Label by clicking the map', and click a few.`;
      const carried = Object.values(classify.labeller.carriedCounts()).reduce((a, b) => a + b, 0);
      status.textContent = carried && classify.labeller.classes.length
        ? `Classified using ${carried} examples carried over from earlier runs, nothing labelled here yet. Check a few and label any it got wrong.  (${classify.status()})`
        : classify.status();
    } catch (e) {
      status.textContent = e instanceof Cancelled ? "Cancelled." : `Could not make objects: ${(e as Error)?.message ?? e}`;
    } finally {
      busy = false;
      makeBtn.textContent = "Generate Embedded Vector Set";
      bar.hidden = true;
    }
  };

  /** Show a run's own objects, or none. */
  function show(id: string | null) {
    layer?.clear();
    layer = null;
    classify.clear();
    count.textContent = "";
    const r = id ? deps.runs.get(id) : null;
    if (!r?.objects) return;
    layer = new ObjectsLayer(deps.app, r.meta.id, `${r.meta.yearA}-${r.meta.yearB}`);
    layer.set(r.objects.polys, r.meta.grid);
    count.textContent = countText(r.objects.polys.length, r.objects.cutoff);
    classify.setObjects(r.objects.vectors, r.objects.polys.length, layer);
  }

  return {
    root,
    runChanged: (id) => { cancel = true; status.textContent = ""; show(id); },
    dispose: () => { cancel = true; classify.clear(); layer?.clear(); },
  };
}
