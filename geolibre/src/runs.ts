// This session's runs, held in memory only. Nothing is written to disk: like the QGIS plugin's
// temp folder, a run is gone when GeoLibre closes unless the user saves it to a file.
import type { Bbox } from "./engine/crs";
import type { Grid } from "./engine/grid";
import type { TileResult } from "./engine/job";
import type { ObjectPoly } from "./engine/objects";
import type { ChangeLayer } from "./map/changelayer";
import type { ObjectsLayer } from "./map/objectslayer";
import type { CellGrid } from "./engine/cells";

export type RunObjects = { polys: ObjectPoly[]; vectors: Float32Array; cutoff: number; minArea: number };

export type RunRecord = {
  id: string; name: string; createdAt: number;
  /** What the user called the area ("" = unnamed: its centre stands in). */
  userName?: string;
  /** Its layer group's name, and GeoLibre's id for that group once made. */
  group?: string; groupId?: string;
  bbox: Bbox; yearA: number; yearB: number; resM: number; grid: Grid;
  histTotal: number[]; complete: boolean; failed: number; tiles: number;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function runName(yearA: number, yearB: number, resM: number, when: Date): string {
  return `${yearA} to ${yearB}, ${resM} m, ${when.getDate()} ${MONTHS[when.getMonth()]} ${when.getFullYear()}`;
}

/** A name for an area nobody named: where it is (dock.py _auto_name). */
export function autoName([w, s, e, n]: Bbox): string {
  const cy = (s + n) / 2, cx = (w + e) / 2;
  return `${Math.abs(cy).toFixed(2)}${cy >= 0 ? "N" : "S"} ${Math.abs(cx).toFixed(2)}${cx >= 0 ? "E" : "W"}`;
}

/** The layer group for a run: "<name>  <A>→<B>" (dock.py _group_name). */
export const groupName = (name: string, auto: string, yearA: number, yearB: number) =>
  `${name.trim() || auto}  ${yearA}→${yearB}`;

/** A group name not already taken: the second becomes "<base> (2)" (dock.py _resolve_group). */
export function resolveGroup(base: string, taken: string[]): string {
  if (!taken.includes(base)) return base;
  let n = 2;
  while (taken.includes(`${base} (${n})`)) n++;
  return `${base} (${n})`;
}

const detailLabel = (resM: number) => (resM === 10 ? "10 m (full)" : `${resM} m`);

/** How the Area list names a run (dock.py _refresh_run_combo). */
export const runLabel = (r: Pick<RunRecord, "userName" | "bbox" | "yearA" | "yearB" | "resM" | "complete">) =>
  `${r.userName?.trim() || autoName(r.bbox)} · ${r.yearA}→${r.yearB} · ${detailLabel(r.resM)}` + (r.complete ? "" : " (incomplete)");

/** A run in this session, with the map layers showing it (they outlive the panel being closed). */
export type SessionRun = {
  meta: RunRecord; tiles: TileResult[]; objects?: RunObjects; cells?: CellGrid[];
  change?: ChangeLayer; objectsLayer?: ObjectsLayer;
};

export class SessionRuns {
  private runs = new Map<string, SessionRun>();

  start(meta: RunRecord): void {
    this.runs.set(meta.id, { meta, tiles: [] });
  }

  addTile(id: string, t: TileResult): void {
    this.runs.get(id)?.tiles.push(t);
  }

  /** Cells read from a run file (a reopened run's tiles do not carry their own). */
  setCells(id: string, cells: CellGrid[]): void {
    const r = this.runs.get(id);
    if (r) r.cells = cells;
  }

  setObjects(id: string, objects: RunObjects): void {
    const r = this.runs.get(id);
    if (r) r.objects = objects;
  }

  get(id: string) {
    return this.runs.get(id);
  }

  /** Remove every run's layers from the map (the plugin is being switched off). */
  clearLayers(): void {
    for (const r of this.runs.values()) {
      r.change?.clear(); r.objectsLayer?.clear();
      r.change = r.objectsLayer = undefined;
    }
  }

  list(): RunRecord[] {
    return [...this.runs.values()].map((r) => r.meta).sort((a, b) => b.createdAt - a.createdAt);
  }
}

/** One per page: survives the panel closing and reopening, not GeoLibre closing. */
export const sessionRuns = new SessionRuns();
