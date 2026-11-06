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
import { drawBox, keepAreaOnTop, showArea } from "../map/drawbox";
import { FolderWriter, listFolderRuns, loadFolderRun, runPrefix, type FolderRun } from "../runfolder";
import { saveBinary } from "../export";
import { cellsOf, readRunFile, runFileName, writeRunFile } from "../runfile";
import { autoName, ensureGroup, groupName, resolveGroup, runLabel, runName, type RunRecord, type SessionRuns } from "../runs";
import { el, row, select } from "./dom";
import type { Step2 } from "./step2";

export const DETAIL: [string, number][] = [["10 m (full)", 10], ["20 m", 20], ["50 m", 50], ["75 m", 75], ["100 m", 100]];
export const YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025];

// Both years of one tile, measured here on a home connection: ~3-4 s at full detail and at coarse
// Detail alike (QGIS's 11 s for a coarse tile does not hold for this reader). The live estimate
// during a run replaces it with this connection's own speed.
const SEC_PER_TILE = 4;

export function durationText(sec: number): string {
  const m = Math.round(sec / 60);
  if (m < 1) return "under a minute";
  if (m < 60) return `about ${m} min`;
  return `about ${Math.floor(m / 60)} h` + (m % 60 ? ` ${m % 60} min` : "");
}

export function estimateText(e: { tiles: number; gb: number; px: number }, g: { width: number; height: number }): string {
  const sec = e.tiles * SEC_PER_TILE;
  return `${e.tiles} tiles, about ${e.gb.toFixed(1)} GB to read, ${durationText(sec)}, output ${g.width} x ${g.height} px`;
}

export function sizeWarning(px: number, detail: string): string {
  return px > MAX_PIXELS
    ? `This area at ${detail} makes a very large map (${Math.round(px / 1e6)} million pixels) and may run out of browser memory. A coarser Detail is safer.`
    : "";
}

/** Seconds per tile over the times given (the plan's, then each tile's), or undefined. */
export function recentPace(times: number[]): number | undefined {
  return times.length > 1 ? (times[times.length - 1] - times[0]) / 1000 / (times.length - 1) : undefined;
}

/** `secPerTile`: the run's recent pace, for the time left. */
export function progressText(e: JobEvent, secPerTile?: number): string {
  if (e.type === "plan") return "Starting: reading the first tile (the slowest one)...";
  if (e.type === "tile") {
    const left = e.done < e.total && secPerTile ? `, ${durationText(secPerTile * (e.total - e.done))} left` : "";
    return `${e.done} of ${e.total} tiles${left}`;
  }
  if (e.cancelled) return "Cancelled. The finished tiles are kept.";
  return e.failed > 0
    ? `Done. ${e.failed} tile(s) could not be downloaded and are left out. Make the change map again to fill them in.`
    : "Done.";
}

// The folder runs save to. A picked folder lasts as long as the app does, so this outlives the panel.
let saveFolder: { id: string; path: string } | null = null;

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
  /** A run is starting: put its after year's imagery under it (unless already there). */
  onRunStart?: (yearB: number) => void;
};

export function buildStep1(deps: Deps): {
  root: HTMLElement; layer(): ChangeLayer | null; current(): string | null; dispose(): void;
  /** Progress bar, Cancel and status: shown under the steps, never inside one. */
  progressRow: HTMLElement;
  /** Set the area as if drawn (also what Open run uses). */
  setArea(bbox: Bbox): void;
  summary(): string | null; hasResult(): boolean;
} {
  const { app, runs, step2 } = deps;
  const map = () => (app as any).getMap?.();
  const root = el("section", "ecd-step");

  const picker = select([["", "New area"]]);
  picker.classList.add("ecd-grow");                 // one line whatever the run is called: no jumping rows
  const nameIn = el("input", "ecd-text") as HTMLInputElement;
  nameIn.title = "What to call this area. Its layers go in a group of this name, so several areas can sit in the map at once. "
    + "Leave it empty and the area's location is used instead.\n\nThis always names the area currently drawn. "
    + "To rename a run you have already made, switch to it under Area first.";
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
  // Progress, Cancel and the status line live OUTSIDE the accordion (as in the QGIS dock): step 1
  // folds itself as soon as the first tile lands, and a bar or a Cancel you can no longer see is
  // no use. The panel puts this row under the steps.
  const cancelBtn = el("button", "ecd-btn", "Cancel") as HTMLButtonElement;
  cancelBtn.title = "Stop after the tile now downloading. Finished tiles are kept.";
  cancelBtn.hidden = true;
  const progressRow = el("div", "ecd-runrow");
  progressRow.append(row(bar, cancelBtn), status);
  const saveBtn = el("button", "ecd-btn", "Save run...") as HTMLButtonElement;
  saveBtn.title = "Runs are temporary. Save one to a file to keep it; Open run brings it back.";
  const openBtn = el("button", "ecd-btn", "Open run...") as HTMLButtonElement;
  const discardBtn = el("button", "ecd-btn", "Discard run") as HTMLButtonElement;
  discardBtn.title = "Remove this run from EMBED-CD and its layers from the map. A file you saved it to is not touched.\n\n"
    + "Removing its layers in the Layers panel only takes it off the map: it stays in the Area list.";
  // Save to folder: needs GeoLibre desktop 3.6.1 or later, so the row is only there when it works.
  const host = app as any;
  const canFolder = !!(host.pickDownloadFolder && host.writeFileToFolder && host.pickLocalDirectoryFiles);
  openBtn.title = canFolder
    ? "Reopen a run. Pick a folder and choose from the runs in it, finished or not. Objects and labels come back too."
    : "Reopen a run from a file made with Save run.";
  const folderIn = el("input", "ecd-text ecd-grow") as HTMLInputElement;
  folderIn.readOnly = true;
  folderIn.placeholder = "(temporary, discarded on exit)";
  folderIn.title = "Optional. Each run writes itself to its own folder inside this one as it goes, so finished tiles survive a "
    + "crash or closing GeoLibre. Its objects and labels are saved there too.";
  const folderBtn = el("button", "ecd-btn", "...") as HTMLButtonElement;
  folderBtn.title = "Choose the folder runs save to.";
  const dest = el("p", "ecd-muted ecd-path", "");
  const destText = document.createTextNode("");
  const destLink = el("button", "ecd-link", "") as HTMLButtonElement;
  dest.append(destText, destLink);
  const showFolder = () => {
    folderIn.value = saveFolder?.path ?? "";
    destText.textContent = saveFolder ? `Results \u2192 ${saveFolder.path} \u00b7 ` : "Results \u2192 memory only, discarded when GeoLibre closes \u00b7 ";
    destLink.textContent = saveFolder ? "stop saving to a folder" : "keep them in a folder";
  };
  showFolder();
  const folderRow = row(el("span", "", "Save to"), folderIn, folderBtn, openBtn);
  const runList = el("div", "ecd-runlist");
  runList.hidden = true;
  folderRow.hidden = dest.hidden = !canFolder;
  root.append(row(el("span", "", "Area"), picker), row(drawBtn, ...(canFolder ? [] : [openBtn]), saveBtn, discardBtn), row(el("span", "", "Name"), nameIn),
    row(el("span", "", "From"), yearA, el("span", "", "To"), yearB),
    row(el("span", "", "Detail"), detail), folderRow, dest, runList, est, warn, row(runBtn));

  let bbox: Bbox | null = null;
  let layer: ChangeLayer | null = null;
  let job: { cancel(): void; done: Promise<void> } | null = null;
  let cancelDraw: (() => void) | null = null;
  let nameForNext = false;                          // the Name field's text is for the next box
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
  // Discard run's "Click again to discard" state (see discardBtn.onclick).
  let disarm = 0;
  function arm(on: boolean): void {
    clearTimeout(disarm);
    discardBtn.textContent = on ? "Click again to discard" : "Discard run";
    discardBtn.classList.toggle("ecd-danger", on);
    if (on) disarm = window.setTimeout(() => arm(false), 5000);
  }
  function refreshPicker(select_?: string) {
    picker.replaceChildren(new Option("New area", ""), ...runs.list().map((r) => new Option(runLabel(r, !runs.onMap(r.id)), r.id)));
    picker.value = select_ ?? "";
    saveBtn.disabled = discardBtn.disabled = !current || !!job;
    const writer = current ? runs.get(current)?.writer : null;
    saveBtn.hidden = !!writer && !writer.failed;     // already in its folder: nothing to save
    arm(false);
  }
  // Layers removed in the Layers panel send no event: read the map afresh as the list opens. A
  // run off the map is not left selected, so choosing it is a change that puts it back.
  picker.onpointerdown = picker.onfocus = () => {
    if (!job) refreshPicker(picker.value && runs.onMap(picker.value) ? picker.value : undefined);
  };

  function show(meta: RunRecord, tiles: TileResult[]) {
    bbox = meta.bbox;
    nameForNext = false;
    nameIn.value = meta.userName ?? "";
    nameIn.placeholder = autoName(meta.bbox);
    current = meta.id;
    yearA.value = String(meta.yearA);
    yearB.value = String(meta.yearB);
    detail.value = DETAIL.find(([, v]) => v === meta.resM)?.[0] ?? detail.value;
    const r = runs.get(meta.id)!;
    // Some of it removed in the Layers panel: choosing the run puts its layers back (as in QGIS).
    if (r.change && !r.change.intact) { r.change.clear(); r.change = undefined; }
    const fresh = !r.change;                         // already on the map: reuse, never add twice
    layer = layerFor(meta);
    if (fresh) {
      for (const t of tiles) layer.addTile(meta.grid, t);
      layer.publish();
    }
    deps.onRunChanged?.(current);                    // after the change map, so objects go on top of it
    showArea(map(), null);                           // a run shows its own extent layer instead
    step2.setHist(Float64Array.from(meta.histTotal.length ? meta.histTotal : sumHist(tiles)));
    layer!.setCutoff(step2.cutoff());
    void refreshEstimate();
  }

  /** A new area: the name of the run on show stays with that run, so a DIFFERENT box clears it
   *  (QGIS _on_area). Anything else in the field was typed for the new area, and stays. */
  function newArea(b: Bbox) {
    const moved = !bbox || b.some((v, i) => v !== bbox![i]);
    const shownName = (current && runs.get(current)?.meta.userName) || "";
    if (moved && !nameForNext && nameIn.value.trim() === shownName) nameIn.value = "";
    nameForNext = false;
    // A different box is a run still to make: the last run's result no longer belongs to step 1,
    // so steps 2 and 3 wait for this one (its layers stay on the map).
    const r = current ? runs.get(current) : null;
    if (r && r.meta.bbox.some((v, i) => v !== b[i])) {
      current = null;
      layer = null;
      deps.onRunChanged?.(null);
      saveBtn.disabled = discardBtn.disabled = true;
      arm(false);
    }
    bbox = b;
    nameIn.placeholder = autoName(b);
    showArea(map(), b);
    deps.onAreaDrawn?.();
    void refreshEstimate();
  }

  /** Renaming applies to the current run only while its area is the one drawn (dock.py _save_meta):
   *  its label, and its group, by moving its layers into a group of the new name. */
  nameIn.oninput = () => deps.onChange?.();          // the step header follows as you type
  nameIn.onchange = () => {
    if (nameForNext) return;                         // typed for the area about to be drawn
    const r = current ? runs.get(current) : null;
    if (!r || job || !bbox || r.meta.bbox.some((v, i) => v !== bbox![i])) return;
    const meta = r.meta;
    meta.userName = nameIn.value.trim();
    const groups = ((app as any).listLayerGroups?.() ?? []) as { id: string; name: string }[];
    const next = resolveGroup(groupName(meta.userName, autoName(meta.bbox), meta.yearA, meta.yearB),
      groups.filter((g) => g.id !== meta.groupId).map((g) => g.name));
    if (next !== meta.group && (app as any).addLayerGroup) {
      const id = (app as any).addLayerGroup(next);
      const ids = [r.change?.changeId, r.change?.coverageId, r.change?.extentId, r.objectsLayer?.id].filter(Boolean) as string[];
      (app as any).moveLayersToGroup?.(ids, id);
      if (meta.groupId) (app as any).removeLayerGroup?.(meta.groupId);
      meta.group = next;
      meta.groupId = id;
      if (r.change) r.change.groupId = id;
      if (r.objectsLayer) r.objectsLayer.groupId = id;
    }
    refreshPicker(meta.id);
    deps.onChange?.();
  };

  /** The run's change layer, made once, in the run's own Layers-panel group. Other runs' layers stay. */
  function layerFor(meta: RunRecord): ChangeLayer {
    const r = runs.get(meta.id)!;
    if (!r.change) {
      ensureGroup(app, meta);
      r.change = new ChangeLayer(app, meta.id, `${meta.yearA}-${meta.yearB}`);
      r.change.groupId = meta.groupId;
    }
    return r.change;
  }

  // Type a name, then press Draw area: the name is for the new area, not a rename of the last
  // run. pointerdown comes before the Name field's change event, so it can say so in time.
  drawBtn.onpointerdown = () => { nameForNext = document.activeElement === nameIn; };
  drawBtn.onclick = () => {
    const m = map();
    if (!m) return;
    cancelDraw?.();
    status.textContent = "Drag a box on the map.";
    cancelDraw = drawBox(m, (b) => {
      cancelDraw = null;
      newArea(b);
      picker.value = "";
      status.textContent = "";
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
      const blob = await writeRunFile(r.meta, r.tiles, cellsOf(r.tiles, r.cells), { objects: r.objects, labelState: r.labelState });
      const res = await saveBinary(file, blob, { "application/zip": [".zip"] });
      if (res.method !== "cancelled") r.meta.saved = true;
      status.textContent = { picker: `Saved ${file}.`, download: `Sent as a download: ${file}.`, host: `Saved ${file}.`, cancelled: "Not saved." }[res.method];
    } catch (e) {
      status.textContent = `Could not save the run: ${(e as Error)?.message ?? e}`;
    }
  };

  async function openZip(f: File) {
    try {
      const { meta, tiles, cells, objects, labelState } = await readRunFile(await f.arrayBuffer());
      meta.saved = true;
      if (!runs.get(meta.id)) {
        meta.groupId = undefined;                   // a fresh session: its group is made anew
        runs.start(meta);
        for (const t of tiles) runs.addTile(meta.id, t);
        runs.setCells(meta.id, cells);
        if (objects) runs.setObjects(meta.id, objects);
        if (labelState) runs.get(meta.id)!.labelState = labelState;
      }
      // Back where the objects were cut, so the map and the outlines agree (QGIS restore).
      if (objects) step2.setCutoff(objects.cutoff);
      show(meta, runs.get(meta.id)!.tiles);
      refreshPicker(meta.id);
      (app as any).fitBounds?.(meta.bbox);           // take you to the run you opened
      status.textContent = `Opened ${f.name}.`;
    } catch (e) {
      status.textContent = `Could not open ${f.name}: ${(e as Error)?.message ?? e}`;
    }
  }

  folderBtn.onclick = async () => {
    try {
      const picked = await host.pickDownloadFolder();
      if (picked) saveFolder = picked;
      showFolder();
      if (current) resumeSaving(current);
      refreshPicker(current ?? undefined);
    } catch (e) {
      status.textContent = `Could not use that folder: ${(e as Error)?.message ?? e}`;
    }
  };
  destLink.onclick = () => {
    if (!saveFolder) return folderBtn.click();
    saveFolder = null;
    showFolder();
  };

  const newWriter = (folder: { id: string; path: string }, meta: RunRecord) => {
    const prefix = (meta.prefix ??= runPrefix(meta));
    // Each run in its own folder. A GeoLibre without subfolders ignores the option and writes them
    // side by side, which still reads back: every file name carries the prefix.
    return new FolderWriter((name, data) => host.writeFileToFolder(folder.id, name, data, { subfolder: prefix }), prefix,
      (why) => { status.textContent = `Saving to the folder stopped: ${why} Save run still works.`; });
  };

  /** A reopened run keeps saving once the folder it was saved in is the one picked (a picked folder
   *  cannot be told from the files Open run reads, and lasts only as long as the app does). */
  function resumeSaving(id: string): boolean {
    const r = runs.get(id);
    if (!r || r.writer || !saveFolder || r.meta.folder !== saveFolder.path) return !!r?.writer;
    r.writer = newWriter(saveFolder, r.meta);
    r.writer.objectsStamp = r.meta.objectsStamp ?? 0;
    return true;
  }

  async function openFolderRun(run: FolderRun) {
    status.textContent = `Reading ${run.tiles} tiles...`;
    try {
      const { meta, tiles, objects, labelState } = await loadFolderRun(run);
      if (!runs.get(meta.id)) {
        runs.start(meta);
        for (const t of tiles) runs.addTile(meta.id, t);
        if (objects) runs.setObjects(meta.id, objects);
        if (labelState) runs.get(meta.id)!.labelState = labelState;
      }
      if (objects) step2.setCutoff(objects.cutoff);  // where they were cut, as a run file does
      show(meta, runs.get(meta.id)!.tiles);
      const saving = resumeSaving(meta.id);
      refreshPicker(meta.id);
      (app as any).fitBounds?.(meta.bbox);
      status.textContent = `Opened ${tiles.length} tiles${objects ? ` and ${objects.polys.length} objects` : ""}.`
        + (meta.complete ? "" : " This run was not finished.")
        + (saving || !meta.folder ? "" : ` To keep saving changes, set Save to to ${meta.folder}.`);
    } catch (e) {
      status.textContent = `Could not open that run: ${(e as Error)?.message ?? e}`;
    }
  }

  /** The runs in a folder to pick from, as QGIS asks: one opens at once, several are listed. */
  function chooseRun(found: { label: string; open(): Promise<void> }[]) {
    if (found.length === 1) return void found[0].open();
    const pick = (label: string, then?: () => void) => {
      const b = el("button", "ecd-btn", label) as HTMLButtonElement;
      b.onclick = () => { runList.hidden = true; then?.(); };
      return b;
    };
    runList.replaceChildren(el("p", "ecd-muted", `${found.length} saved runs in that folder:`),
      ...found.map((r) => pick(r.label, () => void r.open())), pick("Cancel"));
    runList.hidden = false;
    status.textContent = "";
  }

  openBtn.onclick = async () => {
    if (!canFolder) {
      const input = el("input") as HTMLInputElement;
      input.type = "file";
      input.accept = ".zip";
      input.onchange = () => { if (input.files?.[0]) void openZip(input.files[0]); };
      return input.click();
    }
    runList.hidden = true;
    status.textContent = "Reading the folder...";
    try {
      // ponytail: the host reads every file in the folder before we see a name, so a folder of many
      // big runs takes its whole size in memory. Picking one run's own folder reads just that run.
      const files: File[] | null = await host.pickLocalDirectoryFiles();
      if (!files) return void (status.textContent = "");
      const found = (await listFolderRuns(files)).map((r) => ({
        label: `${runLabel(r.meta)}, ${r.tiles} tiles${r.objects ? ", objects" : ""}${r.meta.complete ? "" : ", not finished"}`,
        open: () => openFolderRun(r),
      }));
      for (const f of files) {
        if (/^embed-cd_.*\.zip$/i.test(f.name)) found.push({ label: f.name, open: () => openZip(f) });
      }
      if (found.length) chooseRun(found);
      else status.textContent = "No EMBED-CD runs in that folder.";
    } catch (e) {
      status.textContent = `Could not read that folder: ${(e as Error)?.message ?? e}`;
    }
  };

  // An unsaved run cannot be brought back: the first click turns the button itself into the
  // question for 5 s (a message in the status line below went unseen), a second click discards.
  discardBtn.onclick = () => {
    const r = current ? runs.get(current) : null;
    if (!r || job) return;
    const kept = r.meta.saved || (r.writer && !r.writer.failed);       // its folder has everything
    if (!kept && !discardBtn.classList.contains("ecd-danger")) {
      arm(true);
      status.textContent = r.meta.folder
        ? "Changes since this run stopped saving to its folder would be lost."
        : "This run was never saved, so it cannot be brought back.";
      return;
    }
    arm(false);
    runs.remove(r.meta.id);
    if (r.meta.groupId) (app as any).removeLayerGroup?.(r.meta.groupId);
    current = null;
    layer = null;
    nameIn.value = "";                               // it named the run just discarded
    deps.onRunChanged?.(null);
    refreshPicker();
    status.textContent = `Discarded ${runLabel(r.meta)}.`;
    deps.onChange?.();
  };

  cancelBtn.onclick = () => {
    if (!job) return;
    job.cancel();                                    // stops at once; the end event says so
  };

  runBtn.onclick = () => {
    if (job || !bbox) return;
    const folder = canFolder ? saveFolder : null;
    const spec = { bbox, yearA: Number(yearA.value), yearB: Number(yearB.value), resM: res(), pack: !!folder };
    const id = `run-${Date.now()}`;
    const meta: RunRecord = {
      id, name: runName(spec.yearA, spec.yearB, spec.resM, new Date()), createdAt: Date.now(), userName: nameIn.value.trim(),
      bbox: spec.bbox, yearA: spec.yearA, yearB: spec.yearB, resM: spec.resM, folder: folder?.path,
      grid: null as unknown as Grid, histTotal: [], complete: false, failed: 0, tiles: 0,
    };
    const writer = folder ? newWriter(folder, meta) : null;
    const saving = () => (writer?.failed ? ` Saving to the folder stopped: ${writer.failed} Save run still works.` : "");
    runs.start(meta);
    runs.get(id)!.writer = writer ?? undefined;
    layer = layerFor(meta);
    current = id;
    deps.onRunChanged?.(current);
    const hist = new Float64Array(HIST_BINS);
    // When each recent tile landed: the time left follows the last 10 tiles' pace, so it moves
    // when the run speeds up or slows down (a whole-run average barely moved late in a run).
    const landed: number[] = [];
    // Locked while running: switching area mid-run would send this job's tiles to another layer.
    picker.disabled = drawBtn.disabled = openBtn.disabled = folderBtn.disabled = saveBtn.disabled = discardBtn.disabled = true;
    runList.hidden = true;
    bar.hidden = false;
    bar.removeAttribute("value");                    // busy until the first tile lands
    status.textContent = "Checking coverage...";
    runBtn.disabled = true;
    cancelBtn.hidden = false;
    deps.onRunStart?.(spec.yearB);
    showArea(map(), null);                           // the run's extent layer takes over from the box
    job = deps.startJob(spec, (e) => {
      if (e.type === "error") {
        status.textContent = `The job stopped: ${e.message}`;
        return;
      }
      if (e.type === "plan" || e.type === "tile") landed.push(performance.now());
      if (landed.length > 11) landed.shift();
      status.textContent = progressText(e, recentPace(landed)) + saving();
      if (e.type === "plan") {
        meta.grid = e.grid;
        writer?.manifest(meta);
        refreshPicker(id);
      } else if (e.type === "tile") {
        bar.max = e.total;
        bar.value = e.done;
        layer?.addTile(meta.grid, e.result);
        if (e.packed) writer?.tile(e.done, e.packed);
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
        writer?.manifest(meta);
      }
    });
    const runLayer = layer;
    job.done.then(async () => {
      runLayer?.publish();                           // every tile, once (not once per tile)
      if (writer) {
        const said = status.textContent;
        status.textContent = "Finishing writing to the folder...";
        await writer.done();
        status.textContent = said + (saving() || ` Saved to ${folder!.path}.`);
      }
      job = null;
      picker.disabled = drawBtn.disabled = openBtn.disabled = folderBtn.disabled = false;
      refreshPicker(current ?? undefined);
      bar.hidden = true;
      cancelBtn.hidden = true;
      runBtn.disabled = !bbox;
    });
  };

  // A basemap switch replaces the map style and drops our outline; put it back.
  const reShow = () => showArea(map(), current ? null : bbox);
  map()?.on?.("style.load", reShow);
  const stopKeeping = keepAreaOnTop(map());

  runBtn.disabled = true;
  refreshPicker();
  return {
    root,
    progressRow,
    layer: () => layer,
    current: () => current,
    setArea: (b: Bbox) => newArea(b),
    summary: () => (bbox ? `${nameIn.value.trim() || autoName(bbox)}, ${yearA.value}→${yearB.value}, ${detail.value}` : null),
    hasResult: () => !!layer && layer.count > 0,
    dispose() {
      map()?.off?.("style.load", reShow);
      stopKeeping();
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
