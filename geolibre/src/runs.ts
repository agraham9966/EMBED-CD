// A change map's run, kept in app storage so it reopens from the Area picker.
import type { Bbox } from "./engine/crs";
import type { Grid } from "./engine/grid";
import type { TileResult } from "./engine/job";
import { HIST_BINS } from "./engine/score";
import type { RunMeta, RunStore } from "./storage";

export type RunRecord = RunMeta & {
  bbox: Bbox; yearA: number; yearB: number; resM: number; grid: Grid;
  histTotal: number[]; complete: boolean; failed: number; tiles: number;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function runName(yearA: number, yearB: number, resM: number, when: Date): string {
  return `${yearA} to ${yearB}, ${resM} m, ${when.getDate()} ${MONTHS[when.getMonth()]} ${when.getFullYear()}`;
}

export const saveRunMeta = (store: RunStore, meta: RunRecord) => store.putRun(meta);

export async function listRunRecords(store: RunStore): Promise<RunRecord[]> {
  return (await store.listRuns()) as RunRecord[];
}

// One blob per tile: window (4 x int32), histogram (256 x float64), score (float32), coverage (uint8).
export function saveTile(store: RunStore, runId: string, i: number, t: TileResult): Promise<void> {
  return store.putBlob(runId, `tile-${i}`, new Blob([
    Int32Array.from(t.window), t.hist as Float64Array<ArrayBuffer>,
    t.score as Float32Array<ArrayBuffer>, t.cov as Uint8Array<ArrayBuffer>,
  ]));
}

function readTile(buf: ArrayBuffer): TileResult {
  const win = Array.from(new Int32Array(buf, 0, 4)) as TileResult["window"];
  const n = (win[1] - win[0]) * (win[3] - win[2]);
  const histAt = 16, scoreAt = histAt + HIST_BINS * 8, covAt = scoreAt + n * 4;
  return {
    window: win,
    hist: new Float64Array(buf.slice(histAt, scoreAt)),
    score: new Float32Array(buf.slice(scoreAt, covAt)),
    cov: new Uint8Array(buf.slice(covAt, covAt + n)),
  };
}

/** A run and every tile it finished (a cancelled run simply has fewer). */
export async function loadRun(store: RunStore, runId: string): Promise<{ meta: RunRecord; tiles: TileResult[] }> {
  const meta = (await listRunRecords(store)).find((r) => r.id === runId);
  if (!meta) throw new Error(`no saved run ${runId}`);
  const tiles: TileResult[] = [];
  for (let i = 0; ; i++) {
    const b = await store.getBlob(runId, `tile-${i}`);
    if (!b) break;
    tiles.push(readTile(await b.arrayBuffer()));
  }
  return { meta, tiles };
}
