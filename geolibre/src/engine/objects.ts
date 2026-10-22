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
