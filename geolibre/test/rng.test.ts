import { defaultRng, stratifiedFolds } from "../src/engine/rng";
import { fxJson } from "./fixtures";

const H = fxJson("head.json");

test("default_rng(0) draws the same 64-bit numbers as numpy", () => {
  const r = defaultRng(0);
  expect(H.raw_u64.map(() => r.nextU64().toString())).toEqual(H.raw_u64);
});

test("shuffle permutes exactly as numpy's Generator.shuffle", () => {
  for (const [n, want] of Object.entries(H.shuffles)) {
    const a = Array.from({ length: Number(n) }, (_, i) => i);
    defaultRng(0).shuffle(a);
    expect(a).toEqual(want);
  }
});

test("stratified folds match head._stratified_folds", () => {
  for (const f of H.folds) expect(stratifiedFolds(f.y, f.k, 0)).toEqual(f.fold);
});
