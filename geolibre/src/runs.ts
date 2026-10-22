// This session's runs, held in memory only. Nothing is written to disk: like the QGIS plugin's
// temp folder, a run is gone when GeoLibre closes unless the user saves it to a file.
import type { Bbox } from "./engine/crs";
import type { Grid } from "./engine/grid";
import type { TileResult } from "./engine/job";
import type { ObjectPoly } from "./engine/objects";

export type RunObjects = { polys: ObjectPoly[]; vectors: Float32Array; cutoff: number; minArea: number };

export type RunRecord = {
  id: string; name: string; createdAt: number;
  bbox: Bbox; yearA: number; yearB: number; resM: number; grid: Grid;
  histTotal: number[]; complete: boolean; failed: number; tiles: number;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function runName(yearA: number, yearB: number, resM: number, when: Date): string {
  return `${yearA} to ${yearB}, ${resM} m, ${when.getDate()} ${MONTHS[when.getMonth()]} ${when.getFullYear()}`;
}

export class SessionRuns {
  private runs = new Map<string, { meta: RunRecord; tiles: TileResult[]; objects?: RunObjects }>();

  start(meta: RunRecord): void {
    this.runs.set(meta.id, { meta, tiles: [] });
  }

  addTile(id: string, t: TileResult): void {
    this.runs.get(id)?.tiles.push(t);
  }

  setObjects(id: string, objects: RunObjects): void {
    const r = this.runs.get(id);
    if (r) r.objects = objects;
  }

  get(id: string) {
    return this.runs.get(id);
  }

  list(): RunRecord[] {
    return [...this.runs.values()].map((r) => r.meta).sort((a, b) => b.createdAt - a.createdAt);
  }
}

/** One per page: survives the panel closing and reopening, not GeoLibre closing. */
export const sessionRuns = new SessionRuns();
