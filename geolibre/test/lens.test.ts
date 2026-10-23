import { nearestYear, tileUrl } from "../src/map/eox";
import { CompareLens } from "../src/map/lens";
import { buildPhotos, clearPhotos } from "../src/ui/photos";

test("EOX years: an AlphaEarth year maps to the nearest mosaic, ties to the earlier (basemap.nearest_year)", () => {
  expect(nearestYear(2017)).toBe(2016);
  expect(nearestYear(2019)).toBe(2019);
  expect(nearestYear(2030)).toBe(2025);
  expect(tileUrl(2020)).toBe("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg");
  expect(tileUrl(2016)).toContain("/s2cloudless_3857/");          // EOX's 2016 layer has no year in its name
});

class FakeMap {
  static made: FakeMap[] = [];
  handlers: Record<string, ((e?: any) => void)[]> = {};
  cam: any = null;
  paint: Record<string, unknown> = {};
  removed = false;
  container = document.createElement("div");
  data: unknown = null;
  constructor(public opts: any) {
    FakeMap.made.push(this);
    this.container.append(Object.assign(document.createElement("div"), { className: "maplibregl-control-container" }));
  }
  on(t: string, f: any) { (this.handlers[t] ??= []).push(f); }
  off(t: string, f: any) { this.handlers[t] = (this.handlers[t] ?? []).filter((g) => g !== f); }
  fire(t: string, e?: any) { for (const f of this.handlers[t] ?? []) f(e); }
  getContainer() { return this.container; }
  getCenter() { return { lng: 1, lat: 2 }; }
  getZoom() { return 9; }
  getBearing() { return 0; }
  getPitch() { return 0; }
  getProjection() { return { type: "globe" }; }
  setProjection() {}
  jumpTo(c: any) { this.cam = c; }
  resize() {}
  getSource() { return { setData: (d: unknown) => { this.data = d; } }; }
  getLayer() { return true; }
  setPaintProperty(l: string, p: string, v: unknown) { this.paint[`${l}.${p}`] = v; }
  remove() { this.removed = true; }
}

function setup() {
  FakeMap.made = [];
  const main = new FakeMap({});
  document.body.append(main.container);
  const lens = new CompareLens({ getMap: () => main } as any);
  return { main, lens };
}

test("the lens is a second map on the main map's camera, over the canvas and under the controls; it never takes the pointer", () => {
  const { main, lens } = setup();
  lens.setYears(2017, 2024);
  lens.setActive(true);
  const view = FakeMap.made[1];
  expect(view.opts.interactive).toBe(false);
  expect(view.opts.style.sources.before.tiles[0]).toContain("s2cloudless_3857");
  expect(view.opts.style.sources.after.tiles[0]).toContain("s2cloudless-2024_3857");
  const wrap = main.container.querySelector(".ecd-lens")!;
  expect(wrap.nextElementSibling?.className).toBe("maplibregl-control-container");
  main.fire("move");
  expect(view.cam.zoom).toBe(9);
  lens.setActive(false);
  expect(view.removed).toBe(true);
  expect(main.container.querySelector(".ecd-lens")).toBeNull();
  expect(main.handlers.move).toEqual([]);
  main.container.remove();
});

test("the circle follows the cursor; A shows the after year (not while typing); Shift+wheel resizes", () => {
  const { main, lens } = setup();
  lens.setYears(2019, 2024);
  lens.setActive(true);
  const view = FakeMap.made[1];
  const clip = main.container.querySelector(".ecd-lens-clip") as HTMLElement;
  const tag = main.container.querySelector(".ecd-lens-tag") as HTMLElement;
  main.fire("mousemove", { point: { x: 50, y: 60 } });
  expect(clip.style.clipPath).toBe("circle(100px at 50px 60px)");
  expect(tag.textContent).toBe("2019 · before (hold A: after)");

  window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
  expect(view.paint["before.raster-opacity"]).toBe(0);
  expect(tag.textContent).toBe("2024 · after");
  window.dispatchEvent(new KeyboardEvent("keyup", { key: "a" }));
  expect(view.paint["before.raster-opacity"]).toBe(1);

  const input = document.createElement("input");
  document.body.append(input);
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }));
  expect(lens.after).toBe(false);
  input.remove();

  let told = 0;
  lens.onRadiusChanged = (r) => { told = r; };
  main.container.dispatchEvent(new WheelEvent("wheel", { deltaY: -100, shiftKey: true, cancelable: true }));
  expect(told).toBe(110);
  expect(clip.style.clipPath).toBe("circle(110px at 50px 60px)");

  main.container.dispatchEvent(new MouseEvent("mouseleave"));
  expect(clip.style.clipPath).toBe("circle(0px at 0px 0px)");
  lens.dispose();
  main.container.remove();
});

test("reference imagery: each year is its own layer at the bottom of the Layers panel, toggled off by a second click", () => {
  const layers: { id: string; type: string }[] = [{ id: "bg", type: "background" }, { id: "change", type: "raster" }];
  const added: any[] = [];
  const app: any = {
    listLayers: () => layers,
    addTileLayer: (name: string, url: string, opts: any) => { added.push({ name, url, opts }); layers.push({ id: `p${added.length}`, type: "raster" }); return `p${added.length}`; },
    removeLayer: (id: string) => layers.splice(layers.findIndex((l) => l.id === id), 1),
  };
  const root = buildPhotos(app);
  const btn = (t: string) => [...root.querySelectorAll("button")].find((b) => b.textContent === t)!;
  expect(btn("17").title).toBe("No 2017 mosaic exists: this button shows 2016 instead.");
  btn("17").click();
  expect(added[0]).toMatchObject({ name: "Sentinel-2 cloudless 2016 (EOX)", opts: { beforeLayerId: "change", maxzoom: 18 } });
  expect(btn("17").classList.contains("ecd-on")).toBe(true);
  btn("17").click();
  expect(layers.map((l) => l.id)).toEqual(["bg", "change"]);
  expect(btn("17").classList.contains("ecd-on")).toBe(false);
  btn("24").click();
  clearPhotos(app);
  expect(layers.map((l) => l.id)).toEqual(["bg", "change"]);
});
