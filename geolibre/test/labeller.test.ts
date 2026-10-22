import { Labeller } from "../src/labeller";
import { fx, f32 } from "./fixtures";

const vecs = f32("synthetic_vectors.bin");
const N = vecs.length / 128;

function withClasses() {
  const L = new Labeller();
  L.setObjects(vecs, N);
  L.addClass("cutblock");
  L.addClass("road");
  L.label([2, 3, 5, 6], "cutblock");
  L.label([10, 11, 12], "road");
  return L;
}

test("a user's correction is never revised by the model, whatever refits", () => {
  const L = withClasses();
  const r = L.pred!.findIndex((p, i) => p === "cutblock" && !L.labels.has(i));
  L.label([r], "road");
  expect(L.pred![r]).toBe("road");
  L.setQ(0.3); expect(L.pred![r]).toBe("road");
  L.setBestGuess(true); expect(L.pred![r]).toBe("road");
  L.setFeatures("delta"); expect(L.pred![r]).toBe("road");
  L.addClass("fire"); L.label([20], "fire"); expect(L.pred![r]).toBe("road");
});

test("paused, a label changes that object only", () => {
  const L = withClasses();
  L.setPaused(true);
  const before = [...L.pred!];
  L.label([30], "road");
  const changed = L.pred!.flatMap((p, i) => (p !== before[i] ? [i] : []));
  expect(changed.every((i) => i === 30)).toBe(true);
  expect(L.pred![30]).toBe("road");
  L.setPaused(false);                                  // learning again refits from the labels
  expect(L.head).not.toBeNull();
});

test("undo takes a batch back in one step", () => {
  const L = withClasses();
  L.label([30, 31, 32], "road");
  L.undo();
  for (const r of [30, 31, 32]) expect(L.labels.has(r)).toBe(false);
  expect(L.labels.get(10)).toBe("road");
});

test("labels carry over to new objects as banked examples, and are counted as carried", () => {
  const L = withClasses();
  L.setObjects(vecs, N);                                // e.g. a re-cut or a new area
  expect(L.labels.size).toBe(0);
  expect(L.carriedCounts()).toEqual({ cutblock: 4, road: 3 });
  expect(L.pred!.some((p) => p === "cutblock")).toBe(true);   // still classified, from the carried examples
  expect(L.countLine(N)).toBe(`${N} objects · 0 labelled here · 7 examples carried over from earlier runs`);
});

test("stepping: a class filter walks its labels and predictions; least certain skips answered ones", () => {
  const L = withClasses();
  const rows = L.cycleRows("class", "road");
  expect(rows).toEqual(L.pred!.flatMap((p, i) => (p === "road" ? [i] : [])));
  const uncertain = L.cycleRows("uncertain", null);
  for (const r of [2, 3, 5, 6, 10, 11, 12]) expect(uncertain).not.toContain(r);
  expect(L.cycleRows("labelled", null)).toEqual([2, 3, 5, 6, 10, 11, 12]);
});

test("rename keeps a class's examples; delete drops them", () => {
  const L = withClasses();
  L.setObjects(vecs, N);
  L.label([7], "road");
  L.renameClass("road", "track");
  expect(L.carriedCounts().track).toBe(3);
  expect(L.labels.get(7)).toBe("track");
  L.deleteClass("track");
  expect(L.classes).toEqual(["cutblock"]);
  expect(L.carriedCounts().track).toBeUndefined();
  expect(L.labels.has(7)).toBe(false);
});

test("a QGIS class set loads, switches to its mode, and classifies", () => {
  const L = new Labeller();
  L.setObjects(vecs, N);
  expect(L.features).toBe("after");
  const note = L.loadClassSet(fx("classes_qgis.json").toString("utf8"));
  expect(L.features).toBe("delta");
  expect(note).toContain("Loaded 2 classes (7 examples).");
  expect(L.pred!.some((p) => p === "road")).toBe(true);
  expect(L.colors.cutblock).toBe("#d85a30");
});

test("one class ranks by similarity and says so", () => {
  const L = new Labeller();
  L.setObjects(vecs, N);
  L.addClass("cutblock");
  L.label([2, 5, 9], "cutblock");
  expect(L.report()).toContain("(ranked by similarity)");
});

test("A then B then back to A: A's own labels come back, nothing counted twice (QGIS restore)", () => {
  const L = new Labeller();
  L.setObjects(vecs, N);                                      // run A
  L.addClass("cutblock");
  L.label([2, 5], "cutblock");
  const a = L.snapshot();
  L.setObjects(vecs.slice(), N);                              // run B: A's labels carried as examples
  expect(L.carriedCounts()).toEqual({ cutblock: 2 });
  L.label([9], "cutblock");
  L.setObjects(vecs, N);                                      // back to A ...
  L.restore(a);                                               // ... with A's saved state
  expect([...L.labels.entries()]).toEqual([[2, "cutblock"], [5, "cutblock"]]);
  expect(L.carriedCounts()).toEqual({});
  expect(L.pred![2]).toBe("cutblock");
});

test("a snapshot is plain data that survives JSON (for run files)", () => {
  const L = withClasses();
  L.setFeatures("delta");
  const back = new Labeller();
  back.setObjects(vecs, N);
  back.restore(JSON.parse(JSON.stringify(L.snapshot())));
  expect(back.classes).toEqual(["cutblock", "road"]);
  expect(back.features).toBe("delta");
  expect(back.labels.get(10)).toBe("road");
  expect(back.pred).toEqual(L.pred);
});
