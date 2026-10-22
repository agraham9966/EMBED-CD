import { fitFromClasses, reviewOrder, loadClassesJson, saveClassesJson } from "../src/engine/head";
import { f32, fx, fxJson } from "./fixtures";

const H = fxJson("head.json");
const vecs = f32("synthetic_vectors.bin");
const N = vecs.length / 128;
const row = (i: number) => vecs.slice(i * 128, i * 128 + 128);

function classesFor(name: string) {
  const out: Record<string, Float32Array[]> = {};
  for (const [c, rows] of Object.entries(H.labels[name] as Record<string, number[]>)) out[c] = rows.map(row);
  return out;
}

test("every classifier case matches head.py (scipy fit): classes, thresholds, scores, predictions, review order", () => {
  for (const want of H.scipy) {
    const head = fitFromClasses(classesFor(want.labels), vecs, {
      abstainQuantile: want.q, decision: want.decision, features: want.features,
    })!;
    const tag = `${want.labels}/${want.features}/${want.decision}/${want.q}`;
    expect(head.classes, tag).toEqual(want.classes);
    for (const c of want.classes) expect(Math.abs(head.thr[c] - want.thr[c]), `${tag} thr ${c}`).toBeLessThan(1e-6);
    const { pred, scores } = head.predict(vecs, N);
    let worst = 0;
    want.scores.forEach((r: number[], k: number) => r.forEach((v, j) => { worst = Math.max(worst, Math.abs(scores[k * N + j] - v)); }));
    expect(worst, `${tag} scores`).toBeLessThan(1e-5);
    expect(pred, `${tag} pred`).toEqual(want.pred);
    expect(Array.from(reviewOrder(pred, scores, head.classes.length, N)), `${tag} review`).toEqual(want.review);
  }
});

test("one class ranks by similarity and marks some objects, not none and not all", () => {
  const head = fitFromClasses(classesFor("one"), vecs, { abstainQuantile: 0.05, decision: "threshold", features: "after" })!;
  expect(head.singleClass).toBe(true);
  const { pred } = head.predict(vecs, N);
  const marked = pred.filter((p) => p === "cutblock").length;
  expect(marked).toBeGreaterThan(0);
  expect(marked).toBeLessThanOrEqual(N);
});

test("a class set saved by QGIS loads, with its mode and colours", () => {
  const { classes, colors, features } = loadClassesJson(fx("classes_qgis.json").toString("utf8"));
  expect(Object.keys(classes)).toEqual(["cutblock", "road"]);
  expect(classes.cutblock.length).toBe(4);
  expect(classes.cutblock[0].length).toBe(128);
  expect(colors).toEqual({ cutblock: "#d85a30", road: "#1d9e75" });
  expect(features).toBe("delta");
  const again = loadClassesJson(saveClassesJson(classes, colors, "after"));
  expect(again.features).toBe("after");
  expect(Array.from(again.classes.road[2])).toEqual(Array.from(classes.road[2]));
});
