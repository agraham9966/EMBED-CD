// numpy's default_rng(seed): SeedSequence -> PCG64 (XSL-RR 128/64), and Generator.shuffle.
// Reproduced exactly so the classifier's out-of-fold thresholds use the same folds as
// embed_cd/head.py (_stratified_folds with seed 0). Checked against numpy's own output.

const M32 = (x: number) => x >>> 0;
const INIT_A = 0x43b0d7e5, MULT_A = 0x931e8875, INIT_B = 0x8b51f9dd, MULT_B = 0x58f38ded;
const MIX_MULT_L = 0xca01f9dd, MIX_MULT_R = 0x4973f715, XSHIFT = 16, POOL = 4;
const U64 = (1n << 64n) - 1n, U128 = (1n << 128n) - 1n;
const PCG_MULT = 0x2360ed051fc65da44385df649fcc_f645n;

function seedState(seed: number): bigint[] {
  // SeedSequence(seed).generate_state(4, uint64)
  const entropy = [M32(seed)];
  let hc = INIT_A;
  const hashmix = (v: number) => {
    v = M32(v ^ hc);
    hc = M32(Math.imul(hc, MULT_A));
    v = M32(Math.imul(v, hc));
    return M32(v ^ (v >>> XSHIFT));
  };
  const mix = (x: number, y: number) => {
    const r = M32(M32(Math.imul(MIX_MULT_L, x)) - M32(Math.imul(MIX_MULT_R, y)));
    return M32(r ^ (r >>> XSHIFT));
  };
  const pool = Array.from({ length: POOL }, (_, i) => hashmix(i < entropy.length ? entropy[i] : 0));
  for (let s = 0; s < POOL; s++) for (let d = 0; d < POOL; d++) if (s !== d) pool[d] = mix(pool[d], hashmix(pool[s]));
  let hb = INIT_B;
  const words: number[] = [];
  for (let i = 0; i < 8; i++) {
    let v = pool[i % POOL];
    v = M32(v ^ hb);
    hb = M32(Math.imul(hb, MULT_B));
    v = M32(Math.imul(v, hb));
    words.push(M32(v ^ (v >>> XSHIFT)));
  }
  return [0, 1, 2, 3].map((k) => BigInt(words[2 * k]) | (BigInt(words[2 * k + 1]) << 32n));
}

export function defaultRng(seed: number) {
  const [s0, s1, i0, i1] = seedState(seed);
  const initState = (s0 << 64n) | s1, initSeq = (i0 << 64n) | i1;
  const inc = ((initSeq << 1n) | 1n) & U128;
  let state = 0n;
  const step = () => { state = (state * PCG_MULT + inc) & U128; };
  step();
  state = (state + initState) & U128;
  step();
  let buffered: number | null = null;
  const nextU64 = (): bigint => {
    step();
    const x = ((state >> 64n) ^ state) & U64, rot = state >> 122n;
    return ((x >> rot) | (x << ((64n - rot) & 63n))) & U64;
  };
  const nextU32 = (): number => {
    if (buffered !== null) { const v = buffered; buffered = null; return v; }
    const n = nextU64();
    buffered = Number(n >> 32n);
    return Number(n & 0xffffffffn);
  };
  /** numpy's random_interval: a uniform integer in [0, max] by masked rejection. */
  const interval = (max: number): number => {
    if (max === 0) return 0;
    let mask = max;
    for (const s of [1, 2, 4, 8, 16]) mask |= mask >>> s;
    let v: number;
    do v = (nextU32() & mask) >>> 0; while (v > max);
    return v;
  };
  return {
    nextU64,
    nextU32,
    /** In place, as Generator.shuffle on a 1-D array. */
    shuffle(a: number[]): void {
      for (let i = a.length - 1; i > 0; i--) {
        const j = interval(i);
        [a[i], a[j]] = [a[j], a[i]];
      }
    },
  };
}

/** head._stratified_folds: each class's members (in sorted class order) shuffled, dealt round-robin. */
export function stratifiedFolds(y: string[], k: number, seed = 0): number[] {
  const rng = defaultRng(seed);
  const fold = new Array<number>(y.length).fill(0);
  const classes = [...new Set(y)].sort();
  for (const c of classes) {
    const idx = y.flatMap((v, i) => (v === c ? [i] : []));
    rng.shuffle(idx);
    idx.forEach((row, n) => { fold[row] = n % k; });
  }
  return fold;
}
