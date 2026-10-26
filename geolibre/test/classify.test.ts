import { buildClassify } from "../src/ui/classify";
import { ObjectsLayer } from "../src/map/objectslayer";
import { f32 } from "./fixtures";

const vecs = f32("synthetic_vectors.bin");
const N = vecs.length / 128;

function setup() {
  const handlers: Record<string, ((e: any) => void)[]> = {};
  let hit = 2;
  const map: any = {
    style: { _loaded: true }, on: (ev: string, f: any) => (handlers[ev] ??= []).push(f), off: () => {},
    getSource: () => undefined, addSource() {}, addLayer() {}, getLayer: () => false, setPaintProperty() {}, setFilter() {},
    getCanvas: () => ({ style: {} }), queryRenderedFeatures: () => [{ properties: { id: hit + 1 } }],
    getBounds: () => ({ getEast: () => 10, getWest: () => -10, getNorth: () => 10, getSouth: () => -10 }), easeTo() {}, fitBounds() {},
  };
  const app: any = { getMap: () => map, registerExternalNativeLayer() {}, unregisterExternalNativeLayer() {} };
  let status = "";
  const ui = buildClassify(app, (s) => { status = s; });
  const layer = new ObjectsLayer(app, "r", "x");
  const ring: [number, number][] = [[500000, 5500000], [500000, 5499990], [500010, 5499990], [500010, 5500000], [500000, 5500000]];
  layer.set(Array.from({ length: N }, () => ({ rings: [ring], areaHa: 0.01, chgMean: 0.4, chgMax: 0.5, nPx: 1 })),
    { crs: "EPSG:32610", x0: 0, y0: 0, res: 10, width: 1, height: 1 });
  ui.setObjects(vecs, N, layer);
  const click = (ev = "click", row = 2) => { hit = row; for (const f of handlers[ev] ?? []) f({ point: { x: 1, y: 1 }, preventDefault() {} }); };
  const button = (t: string) => [...ui.root.querySelectorAll("button")].find((b) => (b.textContent || b.getAttribute("aria-label")) === t)!;
  return { ui, click, button, status: () => status };
}

function addClass(ui: ReturnType<typeof buildClassify>, button: (t: string) => HTMLButtonElement, name: string) {
  button("Add").click();
  (ui.root.querySelector(".ecd-nameform input") as HTMLInputElement).value = name;
  button("OK").click();
}

test("add a class, label by clicking, see the counts, undo it", () => {
  const { ui, click, button, status } = setup();
  addClass(ui, button, "cutblock");
  expect(ui.root.querySelector(".ecd-class")!.textContent).toContain("cutblock — 0 labelled, 0 predicted");
  button("Label by clicking the map").click();
  expect(status()).toBe("Click polygons to label them 'cutblock'. Right-click removes a label.");
  click("click", 2);
  click("click", 5);
  expect(ui.labeller.labels.get(5)).toBe("cutblock");
  expect(status()).toMatch(/^labelled 'cutblock'\. .*\(ranked by similarity\)/);
  expect(ui.root.querySelector(".ecd-class")!.textContent).toMatch(/cutblock — 2 labelled, \d+ predicted/);
  click("contextmenu", 5);
  expect(ui.labeller.labels.has(5)).toBe(false);
  button("Undo").click();
  expect(ui.labeller.labels.get(5)).toBe("cutblock");
});

test("a click with no class picked says so; a click on nothing says so", () => {
  const { ui, click, button, status } = setup();
  button("Label by clicking the map").click();
  expect(status()).toBe("Add a class first, then click polygons to fill it.");
  addClass(ui, button, "road");
  button("Label by clicking the map").click();
  const L = ui.labeller as any;
  click("click", -1 as any);                        // the stand-in reports id 0: no object
  expect(status()).toBe("No polygon there.");
  expect(L.labels.size).toBe(0);
});

test("Transition mode names a class from both ends", () => {
  const { ui, button } = setup();
  const mode = [...ui.root.querySelectorAll("select")].find((s) => s.querySelector("option[value=delta]"))!;
  mode.value = "delta";
  mode.dispatchEvent(new Event("change"));
  button("Add").click();
  const [from, to] = ui.root.querySelectorAll(".ecd-nameform input") as NodeListOf<HTMLInputElement>;
  from.value = "forest"; to.value = "clearing";
  button("OK").click();
  expect(ui.labeller.classes).toEqual(["forest → clearing"]);
});

test("stepping walks objects and reports the position", () => {
  const { ui, click, button, status } = setup();
  addClass(ui, button, "cutblock");
  button("Label by clicking the map").click();
  click("click", 2);
  button("Next object").click();
  expect(status()).toMatch(/^1 of \d+ {2}\(least certain first\)$/);
});

test("after Generate a Next line walks you through it and lights the button it means", () => {
  const { ui, click, button } = setup();
  const guide = () => ui.root.querySelector(".ecd-guide")!.textContent!;
  const lit = () => [...ui.root.querySelectorAll(".ecd-next")].map((b) => b.textContent || b.getAttribute("aria-label"));
  expect(guide()).toMatch(/^Next: Click Add/);
  expect(lit()).toEqual(["Add"]);
  addClass(ui, button, "cutblock");
  expect(guide()).toContain("Turn on Label by clicking the map");
  expect(lit()).toEqual(["Label by clicking the map"]);
  button("Label by clicking the map").click();
  click("click", 1);
  expect(guide()).toContain("1 labelled so far");
  expect(lit()).toEqual([]);
  click("click", 2); click("click", 3);
  expect(guide()).toContain("add a second class");
  expect(lit()).toEqual(["Add"]);
  addClass(ui, button, "other");
  expect(guide()).toContain("step through the least certain");
  expect(lit()).toEqual(["Next object"]);
});

test("the lens shortcut (L by default, settable) toggles it, but not while typing, and not once the panel is gone", async () => {
  const { CompareLens } = await import("../src/map/lens");
  // Earlier tests' panels are never disposed, so their listeners are still on the window: count
  // only this panel's lens (the last one made; buildClassify sets its radius straight away).
  let mine: unknown = null;
  const calls: boolean[] = [];
  const spy = vi.spyOn(CompareLens.prototype, "setActive").mockImplementation(function (this: unknown, on: boolean) { if (this === mine) calls.push(on); });
  const spyR = vi.spyOn(CompareLens.prototype, "setRadius").mockImplementation(function (this: unknown) { mine = this; });
  const { ui } = setup();
  const lensB = [...ui.root.querySelectorAll("button")].find((b) => b.textContent?.startsWith("Before/after lens"))!;
  expect(lensB.textContent).toBe("Before/after lensL");               // the key shows on the button
  const keyBox = ui.root.querySelector(".ecd-key") as HTMLInputElement;
  document.body.append(ui.root);
  const press = (key: string, target: EventTarget = document.body, extra: KeyboardEventInit = {}) =>
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...extra }));
  press("l");
  expect(calls).toEqual([true]);
  press("l", document.body, { ctrlKey: true });          // Ctrl+L is the browser's, not ours
  keyBox.value = "a"; keyBox.dispatchEvent(new Event("input"));
  expect(keyBox.value).toBe("");                        // A is the lens's own after-year key
  keyBox.value = "k"; keyBox.dispatchEvent(new Event("input"));
  expect(keyBox.value).toBe("K");
  expect(lensB.textContent).toBe("Before/after lensK");
  press("k", keyBox);                                   // typing in a box is not a shortcut
  press("l");                                           // the old key does nothing now
  expect(calls).toEqual([true]);
  press("K");
  expect(calls).toEqual([true, true]);
  ui.dispose();
  calls.length = 0;
  press("k");                                           // gone with the panel
  expect(calls).toEqual([]);
  ui.root.remove();
  spy.mockRestore();
  spyR.mockRestore();
});
