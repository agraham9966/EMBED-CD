// Pool a tile's embeddings into 160 m cells while both years are in memory, with no threshold
// anywhere, so any object cut later at any cutoff can be given its embedding. Port of
// embed_cd/cells.py (pool). Cells are kept only where they touch the output grid: the browser has
// no disk, and a coarse tile covers 80 km however small the area drawn.
import { COV_OK } from "./score";
import { dequantize } from "./score";
import type { Affine, Cube } from "./source";

export const CELL_M = 160;

export type CellGrid = {
  crs: string; x0: number; y0: number; res: number; width: number; height: number;
  ma: Float32Array; mb: Float32Array;           // 64 values per cell, year A and year B
  n: Float32Array; smean: Float32Array; smax: Float32Array;
};

const LUT = Float32Array.from({ length: 256 }, (_, i) => dequantize(i >= 128 ? i - 256 : i));

/** Means of each cell's usable pixels (usable = coverage says data in both years). */
export function poolCells(
  a: Cube, b: Cube, score: Float32Array, cov: Uint8Array, cellPx: number, t: Affine, crs: string,
  keep?: { r0: number; r1: number; c0: number; c1: number },
): CellGrid {
  const { height: h, width: w } = a;
  const ch = Math.ceil(h / cellPx), cw = Math.ceil(w / cellPx);
  const k = keep ?? { r0: 0, r1: ch, c0: 0, c1: cw };
  const H = k.r1 - k.r0, W = k.c1 - k.c0, n = H * W;
  const ma = new Float32Array(n * 64), mb = new Float32Array(n * 64);
  const cnt = new Float32Array(n), smean = new Float32Array(n), smax = new Float32Array(n);
  const sa = new Float64Array(64), sb = new Float64Array(64);
  for (let ci = k.r0; ci < k.r1; ci++) {
    for (let cj = k.c0; cj < k.c1; cj++) {
      sa.fill(0); sb.fill(0);
      let count = 0, ssum = 0, smx = 0;
      for (let y = ci * cellPx; y < Math.min(h, (ci + 1) * cellPx); y++) {
        for (let x = cj * cellPx; x < Math.min(w, (cj + 1) * cellPx); x++) {
          const p = y * w + x;
          if (cov[p] !== COV_OK) continue;                // padding and unusable pixels count as 0
          count++;
          ssum += score[p];
          if (score[p] > smx) smx = score[p];
          const o = p * 64;
          for (let d = 0; d < 64; d++) {
            sa[d] += LUT[a.data[o + d] & 0xff];
            sb[d] += LUT[b.data[o + d] & 0xff];
          }
        }
      }
      const c = (ci - k.r0) * W + (cj - k.c0), safe = Math.max(count, 1);
      cnt[c] = count;
      smean[c] = ssum / safe;
      smax[c] = smx;
      for (let d = 0; d < 64; d++) {
        ma[c * 64 + d] = sa[d] / safe;
        mb[c * 64 + d] = sb[d] / safe;
      }
    }
  }
  const res = t.res * cellPx;
  return { crs, x0: t.x0 + k.c0 * res, y0: t.y0 - k.r0 * res, res, width: W, height: H, ma, mb, n: cnt, smean, smax };
}
