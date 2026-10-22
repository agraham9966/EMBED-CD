// Turn the change map into objects. Port of embed_cd/objects.py (polygonize): 8-connected
// components numbered in raster-scan order (scipy's), with outlines traced on pixel edges the way
// GDAL Polygonize does (corner points only; a diagonal-only join is one outline that touches
// itself at the shared corner).
import type { Grid } from "./grid";
import { NODATA } from "./score";

export type ObjectPoly = {
  rings: [number, number][][];      // grid CRS; first the outline, then any holes; each ring closed
  areaHa: number; chgMean: number; chgMax: number; nPx: number;
};

type Tick = () => Promise<void> | void;

/** 8-connected labels, 1..n in order of each component's first pixel. */
export async function label8(mask: Uint8Array, w: number, h: number, tick?: Tick): Promise<{ labels: Int32Array; n: number }> {
  const labels = new Int32Array(w * h);
  const parent: number[] = [0];
  const find = (x: number) => {
    while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
    return x;
  };
  const union = (a: number, b: number) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent[Math.max(ra, rb)] = Math.min(ra, rb);
  };
  for (let y = 0; y < h; y++) {
    if (tick && y % 256 === 0) await tick();
    for (let x = 0; x < w; x++) {
      const p = y * w + x;
      if (!mask[p]) continue;
      let l = 0;
      const look = (q: number) => { const v = labels[q]; if (v) { if (l) union(l, v); else l = v; } };
      if (x > 0) look(p - 1);
      if (y > 0) { if (x > 0) look(p - w - 1); look(p - w); if (x < w - 1) look(p - w + 1); }
      if (!l) { l = parent.length; parent.push(l); }
      labels[p] = l;
    }
  }
  // Renumber roots in raster-scan order of first appearance.
  const final = new Int32Array(parent.length);
  let n = 0;
  for (let p = 0; p < labels.length; p++) {
    if (!labels[p]) continue;
    const r = find(labels[p]);
    if (!final[r]) final[r] = ++n;
    labels[p] = final[r];
  }
  return { labels, n };
}

const DX = [1, 0, -1, 0], DY = [0, 1, 0, -1];          // right, down, left, up (screen: y down)

/** The rings of one component, in pixel-corner coordinates, collinear points removed. */
function trace(labels: Int32Array, w: number, h: number, L: number, pixels: number[]): [number, number][][] {
  const ex: number[] = [], ey: number[] = [], ed: number[] = [];
  const at = new Map<number, number[]>();             // start vertex -> edges leaving it
  const V = (x: number, y: number) => y * (w + 1) + x;
  const add = (x: number, y: number, d: number) => {
    const i = ex.length;
    ex.push(x); ey.push(y); ed.push(d);
    const k = V(x, y);
    const list = at.get(k);
    if (list) list.push(i); else at.set(k, [i]);
  };
  const is = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && labels[y * w + x] === L;
  for (const p of pixels) {
    const x = p % w, y = (p - x) / w;
    if (!is(x - 1, y)) add(x, y, 1);                   // west side, going down
    if (!is(x, y + 1)) add(x, y + 1, 0);               // south side, going right
    if (!is(x + 1, y)) add(x + 1, y + 1, 3);           // east side, going up
    if (!is(x, y - 1)) add(x + 1, y, 2);               // north side, going left
  }
  const used = new Uint8Array(ex.length);
  const rings: [number, number][][] = [];
  for (let s = 0; s < ex.length; s++) {
    if (used[s]) continue;                             // edge 0 is the first pixel's west side
    const pts: [number, number][] = [];
    let e = s;
    for (;;) {
      used[e] = 1;
      const nx = ex[e] + DX[ed[e]], ny = ey[e] + DY[ed[e]];
      const d = ed[e];
      // At a corner shared only diagonally, turn right: that joins the two pixels (8-connected).
      let next = -1;
      for (const turn of [1, 0, 3]) {
        const want = (d + turn) % 4;
        const c = (at.get(V(nx, ny)) ?? []).find((i) => ed[i] === want && (!used[i] || i === s));
        if (c !== undefined) { next = c; break; }
      }
      if (ed[next] !== d) pts.push([nx, ny]);           // a corner: the direction changes here
      if (next === s || next < 0) break;
      e = next;
    }
    // Start the ring where GDAL does: the corner the starting edge leaves from.
    const k = pts.findIndex(([x, y]) => x === ex[s] && y === ey[s]);
    const ring = k > 0 ? [...pts.slice(k), ...pts.slice(0, k)] : pts;
    ring.push(ring[0]);
    rings.push(ring);
  }
  return rings;
}

/** Objects at or above the cutoff, at least `minAreaHa` in size. */
export async function polygonize(grid: Grid, score: Float32Array, cutoff: number, minAreaHa: number, tick?: Tick): Promise<ObjectPoly[]> {
  const { width: w, height: h, res } = grid;
  const mask = new Uint8Array(w * h);
  for (let i = 0; i < mask.length; i++) mask[i] = score[i] !== NODATA && score[i] >= cutoff ? 1 : 0;
  const { labels, n } = await label8(mask, w, h, tick);
  const pixels: number[][] = Array.from({ length: n + 1 }, () => []);
  const sum = new Float64Array(n + 1), max = new Float32Array(n + 1).fill(-Infinity);
  const lastRow = new Int32Array(n + 1);
  for (let p = 0; p < labels.length; p++) {
    const l = labels[p];
    if (!l) continue;
    pixels[l].push(p);
    lastRow[l] = (p / w) | 0;
    sum[l] += score[p];
    if (score[p] > max[l]) max[l] = score[p];
  }
  const pxHa = (res * res) / 10000;
  const out: ObjectPoly[] = [];
  // GDAL Polygonize emits an object once the scan has passed its bottom row, so its order is by
  // bottom row, then by first pixel. Python numbers objects in that order and, where two touch
  // the same 160 m cell, the later one owns it; matching the order matches both.
  const order = Array.from({ length: n }, (_, i) => i + 1).sort((a, b) => lastRow[a] - lastRow[b] || a - b);
  for (const l of order) {
    if (tick && out.length % 200 === 0) await tick();
    const count = pixels[l].length;
    if (count * pxHa < minAreaHa) continue;
    const rings = trace(labels, w, h, l, pixels[l]).map((r) =>
      r.map(([x, y]) => [grid.x0 + x * res, grid.y0 - y * res] as [number, number]));
    // The outline is the ring with the largest area; the rest are holes.
    const area = (r: [number, number][]) => {
      let a = 0;
      for (let k = 0; k < r.length - 1; k++) a += r[k][0] * r[k + 1][1] - r[k + 1][0] * r[k][1];
      return Math.abs(a);
    };
    rings.sort((a, b) => area(b) - area(a));
    out.push({ rings, areaHa: count * pxHa, chgMean: sum[l] / count, chgMax: max[l], nPx: count });
  }
  return out;
}

// ---- vectors -----------------------------------------------------------------------------------
import type { CellGrid } from "./cells";
import { transformer } from "./crs";

/** Cells of `g` a polygon touches (GDAL ALL_TOUCHED): every cell its outline passes through, plus
 *  every cell whose centre is inside it. Rings are in the cell grid's CRS. */
function touchedCells(g: CellGrid, rings: [number, number][][], out: (cell: number) => void): void {
  const W = g.width, H = g.height;
  const px = (X: number) => (X - g.x0) / g.res, py = (Y: number) => (g.y0 - Y) / g.res;
  const pr = rings.map((r) => r.map(([X, Y]) => [px(X), py(Y)] as [number, number]));
  const mark = (c: number, r: number) => { if (c >= 0 && r >= 0 && c < W && r < H) out(r * W + c); };
  // Outline: every cell whose INTERIOR a segment passes through. A segment lying exactly on a cell
  // edge touches neither neighbour (measured against GDAL: counting either side over-marks).
  for (const ring of pr) {
    for (let k = 0; k < ring.length - 1; k++) {
      const [x0, y0] = ring[k], [x1, y1] = ring[k + 1];
      const steps = Math.ceil(Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)) * 8) + 1;
      let last = -1;
      for (let s = 0; s < steps; s++) {
        const t = (s + 0.5) / steps, x = x0 + (x1 - x0) * t, y = y0 + (y1 - y0) * t;
        if (Number.isInteger(x) || Number.isInteger(y)) continue;        // on a cell edge
        const c = Math.floor(x), r = Math.floor(y), id = r * W + c;
        if (id !== last) { mark(c, r); last = id; }
      }
    }
  }
  // Inside: cell centres, even-odd over all rings.
  let ymin = Infinity, ymax = -Infinity;
  for (const ring of pr) for (const [, y] of ring) { ymin = Math.min(ymin, y); ymax = Math.max(ymax, y); }
  for (let r = Math.max(0, Math.floor(ymin)); r < Math.min(H, Math.ceil(ymax)); r++) {
    const cy = r + 0.5, xs: number[] = [];
    for (const ring of pr) {
      for (let k = 0; k < ring.length - 1; k++) {
        const [x0, y0] = ring[k], [x1, y1] = ring[k + 1];
        if ((y0 <= cy) !== (y1 <= cy)) xs.push(x0 + ((cy - y0) / (y1 - y0)) * (x1 - x0));
      }
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      for (let c = Math.max(0, Math.ceil(xs[k] - 0.5)); c < Math.min(W, Math.ceil(xs[k + 1] - 0.5)); c++) mark(c, r);
    }
  }
}

/** One burn per cell grid, in object order (1-based ids, 0 = none): where two objects touch the
 *  same cell, the later one wins, as with GDAL's RasterizeLayer. */
export function burnObjects(g: { x0: number; y0: number; res: number; width: number; height: number; crs: string },
  polys: ObjectPoly[], polyCrs: string): Int32Array {
  const f = g.crs === polyCrs ? null : transformer(polyCrs, g.crs);
  const burned = new Int32Array(g.width * g.height);
  polys.forEach((p, i) => {
    const rings = f ? p.rings.map((r) => r.map(([x, y]) => f(x, y))) : p.rings;
    touchedCells(g as CellGrid, rings, (c) => { burned[c] = i + 1; });
  });
  return burned;
}

/** [N*128]: each object's count-weighted mean embedding, year A then year B (objects.attach_vectors).
 *  `poolCutoff` skips cells whose own mean change is below it; an object it empties keeps its
 *  unfiltered vector. `progress` returning false cancels. */
export async function attachVectors(
  polys: ObjectPoly[], polyCrs: string, grids: CellGrid[], poolCutoff: number | null = null,
  progress?: (done: number, total: number) => boolean | Promise<boolean>,
): Promise<Float32Array> {
  const N = polys.length, D = 128;
  const acc = new Float64Array(N * D), accAll = new Float64Array(N * D);
  const weight = new Float64Array(N), weightAll = new Float64Array(N);
  for (let gi = 0; gi < grids.length; gi++) {
    const g = grids[gi];
    if (progress && (await progress(gi * N, grids.length * N)) === false) return new Float32Array(N * D);
    const burned = burnObjects(g, polys, polyCrs);
    for (let c = 0; c < burned.length; c++) {
      const i = burned[c] - 1, w = g.n[c];
      if (i < 0 || !(w > 0)) continue;
      const use = poolCutoff === null || g.smean[c] >= poolCutoff;
      for (let d = 0; d < 64; d++) {
        const va = g.ma[c * 64 + d] * w, vb = g.mb[c * 64 + d] * w;
        accAll[i * D + d] += va; accAll[i * D + 64 + d] += vb;
        if (use) { acc[i * D + d] += va; acc[i * D + 64 + d] += vb; }
      }
      weightAll[i] += w;
      if (use) weight[i] += w;
    }
  }
  const out = new Float32Array(N * D);
  for (let i = 0; i < N; i++) {
    const [src, wt] = weight[i] > 0 ? [acc, weight[i]] : weightAll[i] > 0 ? [accAll, weightAll[i]] : [null, 0];
    if (src) for (let d = 0; d < D; d++) out[i * D + d] = src[i * D + d] / wt;
  }
  return out;
}
