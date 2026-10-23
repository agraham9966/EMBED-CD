import { ClickTool } from "../src/map/clicktool";

function fakeMap(hit: number | null) {
  const handlers: Record<string, ((e: any) => void)[]> = {};
  const canvas = { style: { cursor: "" } }, box = document.createElement("div");
  return {
    canvas, box, handlers,
    getCanvas: () => canvas, getContainer: () => box,
    on: (ev: string, f: any) => (handlers[ev] ??= []).push(f),
    off: (ev: string, f: any) => (handlers[ev] = handlers[ev].filter((g) => g !== f)),
    queryRenderedFeatures: (box: any, opts: any) => {
      expect(box).toEqual([[97, 47], [103, 53]]);              // a 3 px tolerance around the click
      expect(opts.layers).toEqual(["objects-fill"]);
      return hit === null ? [] : [{ properties: { id: hit + 1 } }];
    },
    fire(ev: string) { for (const f of handlers[ev] ?? []) f({ point: { x: 100, y: 50 }, preventDefault() {} }); },
  };
}

test("a left click reports the object's row; a right click asks to remove", () => {
  const map = fakeMap(4), got: [number | null, boolean][] = [];
  const tool = new ClickTool({ getMap: () => map } as any, () => "objects-fill", (row, right) => got.push([row, right]));
  tool.on();
  expect(map.box.className).toBe("ecd-labelling");          // the labelling cursor, by CSS
  tool.on("inspect");
  expect(map.box.className).toBe("ecd-inspecting");
  tool.on();
  map.fire("click"); map.fire("contextmenu");
  expect(got).toEqual([[4, false], [4, true]]);
  tool.off();
  expect(map.box.className).toBe("");
  map.fire("click");
  expect(got.length).toBe(2);
});

test("a click on empty map reports no object", () => {
  const map = fakeMap(null), got: (number | null)[] = [];
  const tool = new ClickTool({ getMap: () => map } as any, () => "objects-fill", (row) => got.push(row));
  tool.on();
  map.fire("click");
  expect(got).toEqual([null]);
});

test("while a GeoLibre map tool owns clicks, ours does nothing", () => {
  const map = fakeMap(4), got: unknown[] = [];
  const tool = new ClickTool({ getMap: () => map, getActiveMapTool: () => "identify" } as any, () => "objects-fill", (r) => got.push(r));
  tool.on();
  map.fire("click");
  expect(got).toEqual([]);
});
