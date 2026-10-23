// The editable head: per-class one-vs-rest detectors that may say "unknown". Port of
// embed_cd/head.py (OvRHead, review_order, class-set JSON). The logistic fit is the Newton path
// head.py uses where scipy is missing; it reaches the same optimum as scipy's L-BFGS-B (measured
// on the fixtures: predictions identical, scores within 5e-7).
import { stratifiedFolds } from "./rng";

export const UNKNOWN = "unknown";
export const DEFAULT_Q = 0.05;
export type Features = "delta" | "after" | "raw";
export type HeadOptions = {
  abstainQuantile?: number; calibrateFolds?: number; minConfidence?: number; C?: number;
  decision?: "threshold" | "argmax"; features?: Features; oodScale?: number | null; minForGate?: number;
};

type Mat = { data: Float64Array; n: number; d: number };
const at = (m: Mat, i: number, j: number) => m.data[i * m.d + j];

function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN;
  const pos = q * (sorted.length - 1), lo = Math.floor(pos), hi = Math.min(lo + 1, sorted.length - 1);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
const sortNum = (a: number[]) => [...a].sort((x, y) => x - y);
const sigmoid = (z: number) => 1 / (1 + Math.exp(-Math.min(60, Math.max(-60, z))));
const logaddexp0 = (t: number) => Math.max(0, t) + Math.log1p(Math.exp(-Math.abs(t)));

/** Solve A x = b for a symmetric positive-definite A (Cholesky). */
function solveSpd(a: Float64Array, b: Float64Array, n: number): Float64Array {
  const L = new Float64Array(n * n);
  for (let i = 0; i < n; i++) {
    for (let j = 0; j <= i; j++) {
      let s = a[i * n + j];
      for (let k = 0; k < j; k++) s -= L[i * n + k] * L[j * n + k];
      L[i * n + j] = i === j ? Math.sqrt(Math.max(s, 1e-300)) : s / L[j * n + j];
    }
  }
  const y = new Float64Array(n), x = new Float64Array(n);
  for (let i = 0; i < n; i++) { let s = b[i]; for (let k = 0; k < i; k++) s -= L[i * n + k] * y[k]; y[i] = s / L[i * n + i]; }
  for (let i = n - 1; i >= 0; i--) { let s = y[i]; for (let k = i + 1; k < n; k++) s -= L[k * n + i] * x[k]; x[i] = s / L[i * n + i]; }
  return x;
}

/** L2 logistic regression with balanced class weights, intercept unregularised (sklearn's objective). */
function fitLogistic(x: Mat, y: Uint8Array, C = 1, maxIter = 500): { b: number; w: Float64Array } {
  const { n, d } = x;
  let n1 = 0;
  for (let i = 0; i < n; i++) n1 += y[i];
  const n0 = n - n1;
  const s = new Float64Array(n), sign = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    s[i] = y[i] === 1 ? n / (2 * Math.max(n1, 1)) : n / (2 * Math.max(n0, 1));
    sign[i] = 2 * y[i] - 1;
  }
  const D = d + 1;
  const obj = (th: Float64Array): [number, Float64Array] => {
    const g = new Float64Array(D);
    let loss = 0, ww = 0;
    for (let j = 1; j < D; j++) { ww += th[j] * th[j]; g[j] = th[j]; }
    for (let i = 0; i < n; i++) {
      let z = th[0];
      for (let j = 0; j < d; j++) z += at(x, i, j) * th[j + 1];
      const yz = sign[i] * z;
      loss += s[i] * logaddexp0(-yz);
      const gz = (-s[i] * sign[i]) / (1 + Math.exp(yz));
      g[0] += C * gz;
      for (let j = 0; j < d; j++) g[j + 1] += C * at(x, i, j) * gz;
    }
    return [0.5 * ww + C * loss, g];
  };
  let theta: Float64Array = new Float64Array(D);
  let [f, g] = obj(theta);
  for (let it = 0; it < maxIter; it++) {
    let gmax = 0;
    for (const v of g) gmax = Math.max(gmax, Math.abs(v));
    if (gmax < 1e-9) break;
    const h = new Float64Array(D * D);
    for (let i = 0; i < n; i++) {
      let z = theta[0];
      for (let j = 0; j < d; j++) z += at(x, i, j) * theta[j + 1];
      const p = 0.5 * (1 + Math.tanh(0.5 * z)), wgt = C * s[i] * p * (1 - p);
      for (let a = 0; a < D; a++) {
        const xa = a === 0 ? 1 : at(x, i, a - 1);
        if (xa === 0) continue;
        for (let c = 0; c <= a; c++) h[a * D + c] += wgt * xa * (c === 0 ? 1 : at(x, i, c - 1));
      }
    }
    for (let a = 0; a < D; a++) for (let c = 0; c < a; c++) h[c * D + a] = h[a * D + c];
    for (let a = 1; a < D; a++) h[a * D + a] += 1;
    for (let a = 0; a < D; a++) h[a * D + a] += 1e-12;
    const step = solveSpd(h, g, D);
    let gs = 0;
    for (let a = 0; a < D; a++) gs += g[a] * step[a];
    let t = 1, next: Float64Array = theta, fNew: number, gNew: Float64Array;
    for (;;) {
      next = theta.map((v, a) => v - t * step[a]);
      [fNew, gNew] = obj(next);
      if (fNew <= f - 1e-4 * t * gs || t < 1e-10) break;
      t *= 0.5;
    }
    theta = next;
    const done = Math.abs(f - fNew) <= 1e-15 * Math.max(1, Math.abs(f));
    f = fNew; g = gNew;
    if (done) break;
  }
  return { b: theta[0], w: theta.subarray(1) };
}

/** Each row of x's Euclidean distance to its nearest row of ex (head.py _nearest, by differences). */
function nearest(x: Mat, ex: Mat, rowsOf?: number[]): number[] {
  const idx = rowsOf ?? Array.from({ length: x.n }, (_, i) => i);
  return idx.map((i) => {
    let best = Infinity;
    for (let e = 0; e < ex.n; e++) {
      let s = 0;
      for (let j = 0; j < x.d; j++) { const d = at(x, i, j) - at(ex, e, j); s += d * d; }
      best = Math.min(best, Math.sqrt(s));
    }
    return best;
  });
}

type Det = { kind: "nn"; ex: Mat; r: number } | { kind: "linear"; b: number; w: Float64Array };

export class OvRHead {
  q: number; calibrateFolds: number; minConfidence: number; C: number;
  decision: "threshold" | "argmax"; features: Features; oodScale: number | null; minForGate: number;
  classes: string[] = [];
  thr: Record<string, number> = {};
  private nIn: number | null = null;
  private mean: Float64Array | null = null;
  private std: Float64Array | null = null;
  private dets: Record<string, Det> = {};
  private centroid: Record<string, Float64Array> = {};
  private radius: Record<string, number> = {};
  private nExamples: Record<string, number> = {};

  constructor(o: HeadOptions = {}) {
    this.q = o.abstainQuantile ?? 0.05;
    this.calibrateFolds = o.calibrateFolds ?? 5;
    this.minConfidence = o.minConfidence ?? 0.5;
    this.C = o.C ?? 1;
    this.decision = o.decision ?? "threshold";
    this.features = o.features ?? "delta";
    this.oodScale = o.oodScale === undefined ? 1.6 : o.oodScale;
    this.minForGate = o.minForGate ?? 5;
  }

  /** Stored [A, B] -> what the detectors see. */
  private transform(x: Float32Array, n: number): Mat {
    const width = x.length / n;
    if (width % 2) throw new Error(`expected [N, 2*depth] stored vectors, got width ${width}`);
    if (this.nIn !== null && width !== this.nIn) throw new Error(`fitted on ${this.nIn}-d stored vectors, asked for ${width}-d`);
    const half = width / 2;
    const d = this.features === "after" ? half : width;
    const out = new Float64Array(n * d);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < half; j++) {
        const a = x[i * width + j], b = x[i * width + half + j];
        if (this.features === "after") out[i * d + j] = b;
        else { out[i * d + j] = a; out[i * d + half + j] = this.features === "delta" ? b - a : b; }
      }
    }
    return { data: out, n, d };
  }

  private standardize(raw: Mat, fit = false): Mat {
    const { n, d } = raw;
    if (fit) {
      this.mean = new Float64Array(d); this.std = new Float64Array(d);
      for (let j = 0; j < d; j++) {
        let m = 0;
        for (let i = 0; i < n; i++) m += at(raw, i, j);
        m /= n;
        let v = 0;
        for (let i = 0; i < n; i++) v += (at(raw, i, j) - m) ** 2;
        const sd = Math.sqrt(v / n);
        this.mean[j] = m; this.std[j] = sd < 1e-12 ? 1 : sd;
      }
    }
    const out = new Float64Array(n * d);
    for (let i = 0; i < n; i++) for (let j = 0; j < d; j++) out[i * d + j] = (at(raw, i, j) - this.mean![j]) / this.std![j];
    return { data: out, n, d };
  }

  private strictFloor(): number {
    const over = Math.max(0, this.q - DEFAULT_Q);
    return this.minConfidence + (1 - this.minConfidence) * Math.min(over / 0.45, 0.8);
  }

  private oofPositive(xs: Mat, yc: Uint8Array, seed: number): number[] {
    let nPos = 0;
    for (const v of yc) nPos += v;
    const folds = Math.min(this.calibrateFolds, nPos, yc.length - nPos);
    const inSample = () => {
      const { b, w } = fitLogistic(xs, yc, this.C);
      return rows(yc, 1).map((i) => sigmoid(dotRow(xs, i, w) + b));
    };
    if (folds < 2) return inSample();
    const fold = stratifiedFolds(Array.from(yc, String), folds, seed);
    const oof = new Float64Array(yc.length).fill(NaN);
    for (let k = 0; k < folds; k++) {
      const tr = rows(fold, k, true), te = rows(fold, k);
      const ytr = Uint8Array.from(tr, (i) => yc[i]);
      if (new Set(ytr).size < 2) continue;
      const { b, w } = fitLogistic(subset(xs, tr), ytr, this.C);
      for (const i of te) oof[i] = sigmoid(dotRow(xs, i, w) + b);
    }
    const pos = rows(yc, 1).map((i) => oof[i]).filter(Number.isFinite);
    return pos.length ? pos : inSample();
  }

  /** How close to one of the examples an object must be to join the class (head.py
   *  _one_class_radius): as close as the examples are to each other, never less than the reach
   *  that takes in the nearest 10% of all objects. */
  private oneClassRadius(ex: Mat, pool: Mat | null): number {
    const q10 = pool && pool.n >= 4 ? quantile(sortNum(nearest(pool, ex)), 0.1) : 0;
    let spacing = 0;
    for (let i = 0; i < ex.n && ex.n >= 2; i++) {
      const others = subset(ex, Array.from({ length: ex.n }, (_, k) => k).filter((k) => k !== i));
      spacing = Math.max(spacing, nearest(ex, others, [i])[0]);
    }
    const r = Math.max(spacing, q10);
    if (r > 0) return r;
    let norms = 0;
    for (let i = 0; i < ex.n; i++) { let s = 0; for (let j = 0; j < ex.d; j++) s += at(ex, i, j) ** 2; norms += Math.sqrt(s); }
    return (norms / ex.n) * 0.25;
  }

  /** Strictness scales the radius: 1 at the default, wider at 0, half at 0.40. */
  private oneClassBar(): number {
    const scale = Math.max(0.1, 1 + (0.05 - this.q) * 1.25);
    return 1 / (1 + scale);
  }

  fit(x: Float32Array, n: number, y: string[], pool: Float32Array | null = null, seed = 0): this {
    this.nIn = x.length / n;
    const raw = this.transform(x, n);
    const xs = this.standardize(raw, true);
    this.classes = [...new Set(y)].sort();
    this.dets = {}; this.thr = {}; this.centroid = {}; this.radius = {}; this.nExamples = {};
    const colMean = new Float64Array(xs.d);
    for (let i = 0; i < n; i++) for (let j = 0; j < xs.d; j++) colMean[j] += at(xs, i, j) / n;
    const dists = Array.from({ length: n }, (_, i) => dist(xs, i, colMean));
    const typical = quantile(sortNum(dists), 0.5) || 1;
    for (const c of this.classes) {
      const m = y.flatMap((v, i) => (v === c ? [i] : []));
      const centre = new Float64Array(xs.d);
      for (const i of m) for (let j = 0; j < xs.d; j++) centre[j] += at(xs, i, j) / m.length;
      const d = m.map((i) => dist(xs, i, centre));
      const spread = d.length > 2 ? quantile(sortNum(d), 0.9) : d.length ? Math.max(...d) : 0;
      this.centroid[c] = centre;
      this.radius[c] = Math.max(spread * (this.oodScale || 1), typical * 0.35);
      this.nExamples[c] = m.length;
    }
    const poolMat = pool ? this.transform(pool, pool.length / this.nIn) : null;
    for (const c of this.classes) {
      const yc = Uint8Array.from(y, (v) => (v === c ? 1 : 0));
      let nPos = 0;
      for (const v of yc) nPos += v;
      if (nPos === 0 || nPos === yc.length) {
        // One class: distance to the NEAREST labelled example, in RAW space (see head.py fit).
        const ex = subset(raw, rows(yc, 1));
        this.dets[c] = { kind: "nn", ex, r: this.oneClassRadius(ex, poolMat) };
        this.thr[c] = this.oneClassBar();
        continue;
      }
      const pos = this.oofPositive(xs, yc, seed);
      this.thr[c] = Math.max(quantile(sortNum(pos), this.q), this.minConfidence, this.strictFloor());
      const { b, w } = fitLogistic(xs, yc, this.C);
      this.dets[c] = { kind: "linear", b, w };
    }
    return this;
  }

  get singleClass(): boolean {
    return this.classes.length === 1;
  }

  /** [C*N], class-major. */
  scores(x: Float32Array, n: number): Float64Array {
    const raw = this.transform(x, n), xs = this.standardize(raw);
    const out = new Float64Array(this.classes.length * n);
    this.classes.forEach((c, k) => {
      const det = this.dets[c];
      if (det.kind === "nn") nearest(raw, det.ex).forEach((d, j) => { out[k * n + j] = 1 / (1 + d / det.r); });
      else for (let j = 0; j < n; j++) out[k * n + j] = sigmoid(dotRow(xs, j, det.w) + det.b);
    });
    return out;
  }

  private near(x: Float32Array, n: number): Uint8Array | null {
    if (!this.oodScale || !Object.keys(this.centroid).length || this.singleClass) return null;
    const xs = this.standardize(this.transform(x, n));
    const out = new Uint8Array(this.classes.length * n);
    this.classes.forEach((c, k) => {
      for (let j = 0; j < n; j++) {
        out[k * n + j] = (this.nExamples[c] ?? 0) < this.minForGate || dist(xs, j, this.centroid[c]) <= this.radius[c] ? 1 : 0;
      }
    });
    return out;
  }

  /** UNKNOWN means no detector was both confident AND familiar. */
  predict(x: Float32Array, n: number): { pred: string[]; scores: Float64Array } {
    const s = this.scores(x, n), C = this.classes.length;
    const pred: string[] = [];
    if (this.decision === "argmax") {
      for (let j = 0; j < n; j++) {
        let best = 0;
        for (let k = 1; k < C; k++) if (s[k * n + j] > s[best * n + j]) best = k;
        pred.push(this.classes[best]);
      }
      return { pred, scores: s };
    }
    const near = this.near(x, n);
    for (let j = 0; j < n; j++) {
      let best = -1, bestV = -1;
      for (let k = 0; k < C; k++) {
        const fires = s[k * n + j] >= this.thr[this.classes[k]] && (!near || near[k * n + j]);
        const v = fires ? s[k * n + j] : -1;
        if (fires && v > bestV) { bestV = v; best = k; }
      }
      pred.push(best < 0 ? UNKNOWN : this.classes[best]);
    }
    return { pred, scores: s };
  }

  confidence(x: Float32Array, n: number): Float64Array {
    const s = this.scores(x, n), C = this.classes.length, out = new Float64Array(n).fill(-Infinity);
    for (let k = 0; k < C; k++) for (let j = 0; j < n; j++) out[j] = Math.max(out[j], s[k * n + j]);
    return out;
  }
}

function rows(a: ArrayLike<number>, v: number, not = false): number[] {
  const out: number[] = [];
  for (let i = 0; i < a.length; i++) if ((a[i] === v) !== not) out.push(i);
  return out;
}
function subset(m: Mat, idx: number[]): Mat {
  const out = new Float64Array(idx.length * m.d);
  idx.forEach((i, r) => out.set(m.data.subarray(i * m.d, (i + 1) * m.d), r * m.d));
  return { data: out, n: idx.length, d: m.d };
}
function dotRow(m: Mat, i: number, w: Float64Array): number {
  let s = 0;
  for (let j = 0; j < m.d; j++) s += at(m, i, j) * w[j];
  return s;
}
function dist(m: Mat, i: number, c: Float64Array): number {
  let s = 0;
  for (let j = 0; j < m.d; j++) s += (at(m, i, j) - c[j]) ** 2;
  return Math.sqrt(s);
}

/** The work-list: abstentions first, then the smallest best-vs-second margin. */
export function reviewOrder(pred: string[], scores: Float64Array, C: number, n: number, locked?: ArrayLike<boolean | number>): number[] {
  const free = Array.from({ length: n }, (_, i) => i).filter((i) => !(locked && locked[i]));
  const key = free.map((j) => {
    const col = Array.from({ length: C }, (_, k) => scores[k * n + j]).sort((a, b) => a - b);
    // Rounded as head.py does, so near-ties fall back to object order in both.
    const margin = Math.round((C > 1 ? col[C - 1] - col[C - 2] : -col[C - 1]) * 1e9) / 1e9;
    return { j, unknown: pred[j] === UNKNOWN, margin };
  });
  key.sort((a, b) => (a.unknown === b.unknown ? a.margin - b.margin : a.unknown ? -1 : 1));
  return key.map((k) => k.j);
}

/** Build a head from {class: vectors}; null when there is nothing to fit. `pool` is every candidate. */
export function fitFromClasses(classes: Record<string, Float32Array[]>, pool: Float32Array | null, opts: HeadOptions = {}): OvRHead | null {
  const names = Object.keys(classes).filter((c) => classes[c].length);
  if (!names.length) return null;
  const width = classes[names[0]][0].length;
  const all = names.flatMap((c) => classes[c]);
  const x = new Float32Array(all.length * width);
  all.forEach((v, i) => x.set(v, i * width));
  const y = names.flatMap((c) => classes[c].map(() => c));
  return new OvRHead(opts).fit(x, all.length, y, pool);
}

/** The class-set JSON QGIS writes (head.save_classes): labelled example vectors, not coefficients. */
export function saveClassesJson(classes: Record<string, Float32Array[]>, colors: Record<string, string>, features: string): string {
  return JSON.stringify({
    version: 1, features: features || "delta",
    classes: Object.entries(classes).map(([name, v]) => ({ name, color: colors[name] ?? null, vectors: v.map((r) => Array.from(r)) })),
  });
}

export function loadClassesJson(text: string): { classes: Record<string, Float32Array[]>; colors: Record<string, string>; features: Features } {
  const p = JSON.parse(text);
  const classes: Record<string, Float32Array[]> = {}, colors: Record<string, string> = {};
  for (const e of p.classes) {
    classes[e.name] = e.vectors.map((v: number[]) => Float32Array.from(v));
    if (e.color) colors[e.name] = e.color;
  }
  return { classes, colors, features: p.features ?? "delta" };
}
