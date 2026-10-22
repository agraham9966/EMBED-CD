// Step 1: area, years, Detail, the size estimate, run/cancel with progress, and the Area picker
// of saved runs. Wording and defaults mirror the QGIS dock.
import { utmCrs, type Bbox } from "../engine/crs";
import { estimate, makeGrid, MAX_PIXELS, type Grid } from "../engine/grid";
import type { JobEvent, TileResult } from "../engine/job";
import { HIST_BINS } from "../engine/score";
import { factorFor } from "../engine/source";
import { listTiles, type TileIndex } from "../engine/tileindex";
import type { GeoLibreAppAPI } from "../host-api";
import type { ClientEvent } from "../jobclient";
import { ChangeLayer } from "../map/changelayer";
import { drawBox, showArea } from "../map/drawbox";
import { saveBinary } from "../export";
import { cellsOf, readRunFile, runFileName, writeRunFile } from "../runfile";
import { runName, type RunRecord, type SessionRuns } from "../runs";
import { el, row, select } from "./dom";
import type { Step2 } from "./step2";

export const DETAIL: [string, number][] = [["10 m (full)", 10], ["20 m", 20], ["50 m", 50], ["75 m", 75], ["100 m", 100]];
export const YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025];

export function estimateText(e: { tiles: number; gb: number; px: number }, g: { width: number; height: number }): string {
  return `${e.tiles} tiles, about ${e.gb.toFixed(1)} GB to read, output ${g.width} x ${g.height} px`;
}

export function sizeWarning(px: number, detail: string): string {
  return px > MAX_PIXELS
    ? `This area at ${detail} makes a very large map (${Math.round(px / 1e6)} million pixels) and may run out of browser memory. A coarser Detail is safer.`
    : "";
}

export function progressText(e: JobEvent): string {
  if (e.type === "plan") return "Starting: reading the first tile (the slowest one)...";
  if (e.type === "tile") return `${e.done} of ${e.total} tiles`;
  if (e.cancelled) return "Cancelled. The finished tiles are kept.";
  return e.failed > 0 ? `Done. ${e.failed} tile(s) could not be read and are left out.` : "Done.";
}

type Deps = {
  app: GeoLibreAppAPI;
  runs: SessionRuns;
  loadIndex: () => Promise<TileIndex>;
  startJob: (spec: any, onEvent: (e: ClientEvent) => void) => { cancel(): void; done: Promise<void> };
  step2: Step2;
  onRunChanged?: (id: string | null) => void;
  /** Anything a step header shows changed (area, years, Detail, a result appearing). */
  onChange?: () => void;
  /** A new area was drawn: the steps may move forward again once it has a result. */
  onAreaDrawn?: () => void;
};

export function buildStep1(deps: Deps): {
  root: HTMLElement; layer(): ChangeLayer | null; current(): string | null; dispose(): void;
  summary(): string | null; hasResult(): boolean;
} {
  const { app, runs, step2 } = deps;
  const map = () => (app as any).getMap?.();
  const root = el("section", "ecd-step");

  const picker = select([["", "New area"]]);
  picker.title = "This session's runs. They are discarded when GeoLibre closes unless you save them.";
  const drawBtn = el("button", "ecd-btn", "Draw area") as HTMLButtonElement;
  drawBtn.title = "Drag a box on the map.";
  const yearA = select(YEARS.map((y) => [String(y), String(y)]), "2019");
  const yearB = select(YEARS.map((y) => [String(y), String(y)]), "2024");
  const detail = select(DETAIL.map(([l]) => [l, l]), "10 m (full)");
  detail.title = "Ground size of each output pixel. Coarser reads a built-in overview: much faster, much less to download.";
  const est = el("p", "ecd-muted", "Draw an area to see what it costs.");
  const warn = el("p", "ecd-warn", "");
  const runBtn = el("button", "ecd-btn ecd-primary", "Make change map") as HTMLButtonElement;
  const bar = el("progress", "ecd-progress") as HTMLProgressElement;
  bar.hidden = true;
  const status = el("p", "ecd-status", "");
  const saveBtn = el("button", "ecd-btn", "Save run...") as HTMLButtonElement;
  saveBtn.title = "Runs are temporary. Save one to a file to keep it; Open run brings it back.";
  const openBtn = el("button", "ecd-btn", "Open run...") as HTMLButtonElement;
  root.append(row(el("span", "", "Area"), picker), row(drawBtn, openBtn, saveBtn),
    row(el("span", "", "From"), yearA, el("span", "", "To"), yearB),
    row(el("span", "", "Detail"), detail), est, warn, row(runBtn), bar, status);

  let bbox: Bbox | null = null;
  let layer: ChangeLayer | null = null;
  let job: { cancel(): void; done: Promise<void> } | null = null;
  let cancelDraw: (() => void) | null = null;
  let index: Promise<TileIndex> | null = null;
  const res = () => DETAIL.find(([l]) => l === detail.value)![1];

  async function refreshEstimate() {
    deps.onChange?.();
    warn.textContent = "";
    runBtn.disabled = !bbox || !!job;
    if (!bbox) return;
    const g = makeGrid(bbox, utmCrs(bbox), res());
    warn.textContent = sizeWarning(g.width * g.height, detail.value);
    est.textContent = "Reading the tile index...";
    try {
      index ??= deps.loadIndex();
      const idx = await index;
      const n = listTiles(idx, bbox, Number(yearA.value), Number(yearB.value), factorFor(res())).all.length;
      est.textContent = n ? estimateText(estimate(g, n), g) : "No AlphaEarth tiles cover this area. Try a different area.";
    } catch (e) {
      index = null;
      est.textContent = `Could not read the tile index: ${(e as Error).message}`;
    }
  }

  let current: string | null = null;              // the run on the map
  function refreshPicker(select_?: string) {
    picker.replaceChildren(new Option("New area", ""),
      ...runs.list().map((r) => new Option(r.complete ? r.name : `${r.name} (incomplete)`, r.id)));
    picker.value = select_ ?? "";
    saveBtn.disabled = !current || !!job;
  }

  function show(meta: RunRecord, tiles: TileResult[]) {
    bbox = meta.bbox;
    current = meta.id;
    deps.onRunChanged?.(current);
    yearA.value = String(meta.yearA);
    yearB.value = String(meta.yearB);
    detail.value = DETAIL.find(([, v]) => v === meta.resM)?.[0] ?? detail.value;
    showArea(map(), bbox);
    newLayer(meta.id, meta.yearA, meta.yearB);
    for (const t of tiles) layer!.addTile(meta.grid, t);
    step2.setHist(Float64Array.from(meta.histTotal.length ? meta.histTotal : sumHist(tiles)));
    layer!.setCutoff(step2.cutoff());
    void refreshEstimate();
  }

  function newLayer(id: string, a: number, b: number) {
    layer?.clear();
    layer = new ChangeLayer(app, id, `${a}-${b}`);
  }

  drawBtn.onclick = () => {
    const m = map();
    if (!m) return;
    cancelDraw?.();
    status.textContent = "Drag a box on the map.";
    cancelDraw = drawBox(m, (b) => {
      cancelDraw = null;
      bbox = b;
      deps.onAreaDrawn?.();
      picker.value = "";
      status.textContent = "";
      void refreshEstimate();
    });
  };
  for (const s of [yearA, yearB, detail]) s.onchange = () => void refreshEstimate();

  picker.onchange = () => {
    const r = picker.value ? runs.get(picker.value) : null;
    if (!r) return;
    show(r.meta, r.tiles);
    status.textContent = r.meta.complete ? `Showing ${r.tiles.length} tiles.` : `Showing ${r.tiles.length} tiles. This run was not finished.`;
    refreshPicker(r.meta.id);
  };

  saveBtn.onclick = async () => {
    const r = current ? runs.get(current) : null;
    if (!r) return;
    status.textContent = "Preparing the file...";
    try {
      const file = runFileName(r.meta);
      const res = await saveBinary(file, await writeRunFile(r.meta, r.tiles, cellsOf(r.tiles, r.cells)), { "application/zip": [".zip"] });
      status.textContent = { picker: `Saved ${file}.`, download: `Sent as a download: ${file}.`, host: `Saved ${file}.`, cancelled: "Not saved." }[res.method];
    } catch (e) {
      status.textContent = `Could not save the run: ${(e as Error)?.message ?? e}`;
    }
  };

  openBtn.onclick = () => {
    const input = el("input") as HTMLInputElement;
    input.type = "file";
    input.accept = ".zip";
    input.onchange = async () => {
      const f = input.files?.[0];
      if (!f) return;
      try {
        const { meta, tiles, cells } = await readRunFile(await f.arrayBuffer());
        if (!runs.get(meta.id)) {
          runs.start(meta);
          for (const t of tiles) runs.addTile(meta.id, t);
          runs.setCells(meta.id, cells);
        }
        show(meta, runs.get(meta.id)!.tiles);
        refreshPicker(meta.id);
        status.textContent = `Opened ${f.name}.`;
      } catch (e) {
        status.textContent = `Could not open ${f.name}: ${(e as Error)?.message ?? e}`;
      }
    };
    input.click();
  };

  runBtn.onclick = () => {
    if (job) {
      job.cancel();
      status.textContent = "Stopping after the current tile...";
      return;
    }
    if (!bbox) return;
    const spec = { bbox, yearA: Number(yearA.value), yearB: Number(yearB.value), resM: res() };
    const id = `run-${Date.now()}`;
    const meta: RunRecord = {
      id, name: runName(spec.yearA, spec.yearB, spec.resM, new Date()), createdAt: Date.now(),
      ...spec, grid: null as unknown as Grid, histTotal: [], complete: false, failed: 0, tiles: 0,
    };
    newLayer(id, spec.yearA, spec.yearB);
    current = id;
    deps.onRunChanged?.(current);
    const hist = new Float64Array(HIST_BINS);
    // Locked while running: switching area mid-run would send this job's tiles to another layer.
    picker.disabled = drawBtn.disabled = openBtn.disabled = saveBtn.disabled = true;
    bar.hidden = false;
    bar.removeAttribute("value");                    // busy until the first tile lands
    status.textContent = "Checking coverage...";
    runBtn.textContent = "Cancel";
    job = deps.startJob(spec, (e) => {
      if (e.type === "error") {
        status.textContent = `The job stopped: ${e.message}`;
        return;
      }
      status.textContent = progressText(e);
      if (e.type === "plan") {
        meta.grid = e.grid;
        runs.start(meta);
        refreshPicker(id);
      } else if (e.type === "tile") {
        bar.max = e.total;
        bar.value = e.done;
        layer?.addTile(meta.grid, e.result);
        for (let i = 0; i < HIST_BINS; i++) hist[i] += e.result.hist[i];
        if (meta.tiles === 0) step2.setHist(hist);
        layer?.setCutoff(step2.cutoff());
        meta.tiles++;
        runs.addTile(id, e.result);
        deps.onChange?.();
      } else {
        meta.complete = !e.cancelled;
        meta.failed = e.failed;
        meta.histTotal = Array.from(e.histTotal);
        step2.setHist(Float64Array.from(e.histTotal));
        if (!e.cancelled && meta.tiles > 0) step2.auto();       // as the QGIS run reports AUTO
      }
    });
    job.done.then(() => {
      job = null;
      picker.disabled = drawBtn.disabled = openBtn.disabled = false;
      refreshPicker(current ?? undefined);
      bar.hidden = true;
      runBtn.textContent = "Make change map";
      runBtn.disabled = !bbox;
    });
  };

  // A basemap switch replaces the map style and drops our outline; put it back.
  const reShow = () => showArea(map(), bbox);
  map()?.on?.("style.load", reShow);

  runBtn.disabled = true;
  refreshPicker();
  return {
    root,
    layer: () => layer,
    current: () => current,
    summary: () => (bbox ? `${yearA.value}→${yearB.value}, ${detail.value}` : null),
    hasResult: () => !!layer && layer.count > 0,
    dispose() {
      map()?.off?.("style.load", reShow);
      cancelDraw?.();
      job?.cancel();
    },
  };
}

function sumHist(tiles: TileResult[]): Float64Array {
  const h = new Float64Array(HIST_BINS);
  for (const t of tiles) for (let i = 0; i < HIST_BINS; i++) h[i] += t.hist[i];
  return h;
}
