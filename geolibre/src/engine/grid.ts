// One output grid for the whole job, and each tile's exact window in it. Port of embed_cd/grid.py.
import { transformBounds, type Bbox } from "./crs";

export type Grid = { crs: string; x0: number; y0: number; res: number; width: number; height: number };
export type Window = [r0: number, r1: number, c0: number, c1: number];

/** Output grid covering a lon/lat box, snapped to a whole multiple of the resolution. */
export function makeGrid(bbox: Bbox, dstCrs: string, resM: number): Grid {
  const [left, bottom, right, top] = transformBounds("EPSG:4326", dstCrs, bbox);
  const x0 = Math.floor(left / resM) * resM;
  const y0 = Math.ceil(top / resM) * resM;
  return {
    crs: dstCrs, x0, y0, res: resM,
    width: Math.max(1, Math.ceil((right - x0) / resM)),
    height: Math.max(1, Math.ceil((y0 - bottom) / resM)),
  };
}

/** The window of the grid covered by bounds given in srcCrs, or null when outside. Every edge is
 *  ROUNDED, so two tiles sharing an edge round it to the same column: no seam, no overlap. */
export function windowForBounds(g: Grid, bounds: Bbox, srcCrs: string): Window | null {
  const [left, bottom, right, top] = srcCrs === g.crs ? bounds : transformBounds(srcCrs, g.crs, bounds);
  // Python's round() is banker's rounding; edges at exactly .5 px are not expected, but match it.
  const r = (v: number) => {
    const f = Math.floor(v), d = v - f;
    return d === 0.5 ? (f % 2 === 0 ? f : f + 1) : Math.round(v);
  };
  let c0 = r((left - g.x0) / g.res), c1 = r((right - g.x0) / g.res);
  let r0 = r((g.y0 - top) / g.res), r1 = r((g.y0 - bottom) / g.res);
  c0 = Math.max(0, c0); r0 = Math.max(0, r0);
  c1 = Math.min(g.width, c1); r1 = Math.min(g.height, r1);
  if (c1 <= c0 || r1 <= r0) return null;
  return [r0, r1, c0, c1];
}

export const BYTES_PER_TILE_YEAR = 67e6;   // 1024 x 1024 px x 64 int8 channels
export const MAX_PIXELS = 60e6;            // past this the browser may run out of memory

/** Up-front honesty: output size and download volume before committing. */
export function estimate(g: Grid, nTiles: number) {
  const px = g.width * g.height;
  return { px, tiles: nTiles, gb: (nTiles * 2 * BYTES_PER_TILE_YEAR) / 1e9, tooBig: px > MAX_PIXELS };
}
