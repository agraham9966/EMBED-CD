// The change job: tiles fetched, scored, reprojected into one output grid. Port of
// embed_cd/job.py (process_tile, run) plus the VRT's mosaic rules (embed_cd/vrt.py).
import type { GeoTIFF } from "geotiff";
import { utmCrs, type Bbox } from "./crs";
import { estimate, makeGrid, windowForBounds, type Grid, type Window } from "./grid";
import { reprojectInto } from "./reproject";
import {
  changeScore, coverage, histogram, validMask, COV_NO_TILE, HIST_BINS, NODATA,
} from "./score";
import { factorFor, fetchTile, type Affine, type Cube } from "./source";
import { listTiles, TILE_PX, type Tile, type TileIndex } from "./tileindex";

export type TileResult = { window: Window; score: Float32Array; cov: Uint8Array; hist: Float64Array };
export type JobSpec = { bbox: Bbox; yearA: number; yearB: number; resM: number };
export type JobEvent =
  | { type: "plan"; grid: Grid; tiles: number; partial: number; gb: number }
  | { type: "tile"; done: number; total: number; result: TileResult }
  | { type: "end"; histTotal: Float64Array; failed: number; cancelled: boolean };
export type JobDeps = {
  index: TileIndex;
  open: (url: string) => Promise<GeoTIFF>;
  tilePx?: number;
};

type Fetched = { cube: Cube; crs: string; transform: Affine } | null;

async function tryFetch(deps: JobDeps, tile: Tile, year: number, factor: number, signal?: AbortSignal):
  Promise<{ got: Fetched; failed: boolean }> {
  const row = deps.index.rowFor(tile, year);
  if (!row) return { got: null, failed: false };               // no COG for this year here
  try {
    return { got: await fetchTile(row, tile, factor, deps.open, signal), failed: false };
  } catch (e) {
    if (signal?.aborted) throw e;
    // Python treats an unreadable year as missing; we do the same but COUNT it, so the user
    // hears that part of the map is missing because of a failed download, not a data gap.
    return { got: null, failed: true };
  }
}

async function processTile(tile: Tile, spec: JobSpec, grid: Grid, factor: number, deps: JobDeps,
  signal?: AbortSignal): Promise<{ result: TileResult | null; failed: number }> {
  const [ra, rb] = await Promise.all([
    tryFetch(deps, tile, spec.yearA, factor, signal),
    tryFetch(deps, tile, spec.yearB, factor, signal),
  ]);
  const failed = Number(ra.failed) + Number(rb.failed);
  const a = ra.got, b = rb.got;
  if (!a && !b) return { result: null, failed };
  const ref = (a ?? b)!;
  const { height: h0, width: w0 } = ref.cube;
  const t = ref.transform;
  // Place the tile by its ACTUAL fetched bounds, never by its name (a fetch may come back short
  // at a COG's edge).
  const win = windowForBounds(grid, [t.x0, t.y0 - h0 * t.res, t.x0 + w0 * t.res, t.y0], ref.crs);
  if (!win) return { result: null, failed };
  let score: Float32Array, cov: Uint8Array;
  const sameShape = a && b && a.cube.height === b.cube.height && a.cube.width === b.cube.width;
  if (sameShape) {
    ({ score, cov } = changeScore(a!.cube, b!.cube));
  } else {
    // One year missing: a nodata tile that RECORDS WHY, not a hole that looks like "no change".
    const n = h0 * w0;
    score = new Float32Array(n).fill(NODATA);
    const ok = (c: Fetched) => (c && c.cube.height === h0 && c.cube.width === w0 ? validMask(c.cube) : null);
    cov = coverage(ok(a), ok(b), n);
  }
  const [r0, r1, c0, c1] = win;
  const dstT = { x0: grid.x0 + c0 * grid.res, y0: grid.y0 - r0 * grid.res, res: grid.res };
  const shape: [number, number] = [r1 - r0, c1 - c0];
  const scOut = reprojectInto(score, [h0, w0], ref.crs, t, grid.crs, dstT, shape, NODATA, "average");
  const covF = reprojectInto(Float32Array.from(cov), [h0, w0], ref.crs, t, grid.crs, dstT, shape,
    COV_NO_TILE, "nearest");
  const covOut = Uint8Array.from(covF);
  // Coverage is the authority on what this tile reached: no score on its "no tile" rim.
  for (let i = 0; i < scOut.length; i++) if (covOut[i] === COV_NO_TILE) scOut[i] = NODATA;
  return { result: { window: win, score: scOut, cov: covOut, hist: histogram(scOut) }, failed };
}

/** Run the job, yielding a plan, then each tile as it lands, then the end. */
export async function* runJob(spec: JobSpec, deps: JobDeps, signal?: AbortSignal): AsyncGenerator<JobEvent> {
  const factor = factorFor(spec.resM);
  const { all, partial } = listTiles(deps.index, spec.bbox, spec.yearA, spec.yearB, factor,
    deps.tilePx ?? TILE_PX);
  const grid = makeGrid(spec.bbox, utmCrs(spec.bbox), spec.resM);
  yield { type: "plan", grid, tiles: all.length, partial: partial.length, gb: estimate(grid, all.length).gb };
  const histTotal = new Float64Array(HIST_BINS);
  let failed = 0, done = 0;
  for (const tile of all) {
    if (signal?.aborted) break;
    let out: { result: TileResult | null; failed: number };
    try {
      out = await processTile(tile, spec, grid, factor, deps, signal);
    } catch (e) {
      if (signal?.aborted) break;
      out = { result: null, failed: 1 };                // a bad tile must not kill the job
    }
    failed += out.failed;
    done++;
    if (out.result) {
      for (let i = 0; i < HIST_BINS; i++) histTotal[i] += out.result.hist[i];
      yield { type: "tile", done, total: all.length, result: out.result };
    }
  }
  yield { type: "end", histTotal, failed, cancelled: !!signal?.aborted };
}

/** The VRT's mosaic rules for one window of the grid. Score: tiles in order, each painting only
 *  where it has a value. Coverage: tiles in order painting where they reach (non-zero), then any
 *  real score forces "data in both years", so an EMPTY neighbour from the other UTM zone can never
 *  paint "no data" over real data (embed_cd/vrt.py, _BANDS). */
export function compose(win: Window, tiles: TileResult[]): { score: Float32Array; cov: Uint8Array } {
  const [r0, r1, c0, c1] = win, h = r1 - r0, w = c1 - c0;
  const score = new Float32Array(h * w).fill(NODATA), cov = new Uint8Array(h * w);
  for (const t of tiles) {
    const [tr0, tr1, tc0, tc1] = t.window, tw = tc1 - tc0;
    const y0 = Math.max(r0, tr0), y1 = Math.min(r1, tr1), x0 = Math.max(c0, tc0), x1 = Math.min(c1, tc1);
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) {
        const s = (y - tr0) * tw + (x - tc0), d = (y - r0) * w + (x - c0);
        if (t.score[s] !== NODATA) score[d] = t.score[s];
        if (t.cov[s] !== COV_NO_TILE) cov[d] = t.cov[s];
      }
    }
  }
  for (let i = 0; i < score.length; i++) if (score[i] !== NODATA) cov[i] = 1;
  return { score, cov };
}

/** The whole grid composed, as the Python VRT reads back. */
export function mosaic(grid: Grid, tiles: TileResult[]) {
  return compose([0, grid.height, 0, grid.width], tiles);
}
