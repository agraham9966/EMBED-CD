// Change scoring for one tile pair. Port of embed_cd/score.py.
// Absolute cosine distance (never stretched per tile), missing data as a CODE, never 0.
import type { Cube } from "./source";

export const NODATA = -1;
export const COV_NO_TILE = 0, COV_OK = 1, COV_MISSING_A = 2, COV_MISSING_B = 3, COV_MISSING_BOTH = 4;
export const HIST_BINS = 256;
const EPS = 1e-8;

/** int8 -> the analysis-ready value: sign(v) * (v/127.5)^2, in float32 like numpy. */
const LUT = new Float32Array(256);
for (let v = -128; v < 128; v++) {
  const x = Math.fround(v / 127.5);
  LUT[v & 0xff] = Math.fround(Math.fround(x * x) * Math.sign(v));
}
export const dequantize = (v: number): number => (v === -128 ? 0 : LUT[v & 0xff]);

/** Per pixel: usable (no -128 and a non-zero vector)? */
export function validMask(c: Cube): Uint8Array {
  const n = c.height * c.width, out = new Uint8Array(n), d = c.data;
  for (let p = 0, o = 0; p < n; p++, o += 64) {
    let ss = 0, bad = false;
    for (let k = 0; k < 64; k++) {
      const v = d[o + k];
      if (v === -128) { bad = true; break; }
      const x = LUT[v & 0xff];
      ss += x * x;
    }
    out[p] = !bad && ss > 0 ? 1 : 0;
  }
  return out;
}

/** WHY each pixel is (un)usable. A missing year is passed as null. */
export function coverage(va: Uint8Array | null, vb: Uint8Array | null, n: number): Uint8Array {
  const out = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const a = va ? va[i] : 0, b = vb ? vb[i] : 0;
    out[i] = a && b ? COV_OK : !a && b ? COV_MISSING_A : a && !b ? COV_MISSING_B : COV_MISSING_BOTH;
  }
  return out;
}

/** (score 0..1 with NODATA where unusable, coverage) for two cubes on the same grid. */
export function changeScore(a: Cube, b: Cube): { score: Float32Array; cov: Uint8Array } {
  if (a.height !== b.height || a.width !== b.width) {
    throw new Error(`tile years differ in shape: ${a.height}x${a.width} vs ${b.height}x${b.width}`);
  }
  const n = a.height * a.width;
  const va = validMask(a), vb = validMask(b);
  const cov = coverage(va, vb, n);
  const score = new Float32Array(n).fill(NODATA);
  const A = a.data, B = b.data;
  for (let p = 0, o = 0; p < n; p++, o += 64) {
    if (!(va[p] && vb[p])) continue;
    let aa = 0, bb = 0, ab = 0;
    for (let k = 0; k < 64; k++) {
      const x = LUT[A[o + k] & 0xff], y = LUT[B[o + k] & 0xff];
      aa += x * x; bb += y * y; ab += x * y;
    }
    const cos = ab / (Math.max(Math.sqrt(aa), EPS) * Math.max(Math.sqrt(bb), EPS));
    score[p] = Math.min(1, Math.max(0, (1 - cos) * 0.5));
  }
  return { score, cov };
}

/** Counts of valid scores over a fixed 0..1 range (numpy's binning, last bin closed), so
 *  per-tile histograms can simply be added up. */
export function histogram(score: Float32Array): Float64Array {
  const h = new Float64Array(HIST_BINS);
  for (let i = 0; i < score.length; i++) {
    const v = score[i];
    if (v === NODATA || !(v >= 0 && v <= 1)) continue;
    let k = Math.floor(v * HIST_BINS);
    if (k >= HIST_BINS) k = HIST_BINS - 1;
    h[k]++;
  }
  return h;
}

/** Otsu's cutoff from accumulated counts, as a score in 0..1. */
export function otsu(hist: Float64Array): number {
  let total = 0;
  for (const c of hist) total += c;
  if (total <= 0) return 0.5;
  const centers = Array.from({ length: HIST_BINS }, (_, i) => (i + 0.5) / HIST_BINS);
  let csumAll = 0;
  for (let i = 0; i < HIST_BINS; i++) csumAll += hist[i] * centers[i];
  let w0 = 0, csum = 0, best = -Infinity, at = -1;
  for (let i = 0; i < HIST_BINS; i++) {
    w0 += hist[i];
    csum += hist[i] * centers[i];
    const w1 = total - w0;
    const between = w0 * w1 * (csum / w0 - (csumAll - csum) / w1) ** 2;
    if (Number.isFinite(between) && between > best) { best = between; at = i; }
  }
  if (at < 0 || best <= 0) {
    // numpy.percentile(..., 95) over the bin centres repeated by count (linear interpolation)
    const pos = 0.95 * (total - 1);
    const value = (rank: number) => {
      let seen = 0;
      for (let i = 0; i < HIST_BINS; i++) { seen += hist[i]; if (rank < seen) return centers[i]; }
      return centers[HIST_BINS - 1];
    };
    const lo = Math.floor(pos);
    return value(lo) + (value(Math.min(lo + 1, total - 1)) - value(lo)) * (pos - lo);
  }
  return centers[at];
}

/** Share of valid pixels at or above a cutoff, for the "% of the area changed" line. */
export function fractionAbove(hist: Float64Array, t: number): number {
  let total = 0;
  for (const c of hist) total += c;
  if (total <= 0) return 0;
  const idx = Math.min(HIST_BINS, Math.max(0, pyRound(t * HIST_BINS)));
  let above = 0;
  for (let i = idx; i < HIST_BINS; i++) above += hist[i];
  return above / total;
}

/** Python's round(): halves go to the even neighbour. */
export function pyRound(v: number): number {
  const f = Math.floor(v), d = v - f;
  return d === 0.5 ? (f % 2 === 0 ? f : f + 1) : Math.round(v);
}
