import { drawBox, keepAreaOnTop, showArea } from "../src/map/drawbox";

function setup() {
  const handlers: Record<string, ((e: any) => void)[]> = {};
  const box = document.createElement("div");
  const canvas: any = { style: {}, getBoundingClientRect: () => ({ left: 100, top: 50, width: 400, height: 300 }) };
  const map: any = {
    style: { _loaded: false }, getCanvas: () => canvas, getContainer: () => box,
    dragPan: { disable() {}, enable() {} }, boxZoom: { disable() {}, enable() {} },
    unproject: ([x, y]: number[]) => ({ lng: x / 10, lat: -y / 10 }),       // 10 px a degree, y down
    on: (ev: string, f: any) => (handlers[ev] ??= []).push(f),
    off: (ev: string, f: any) => (handlers[ev] = handlers[ev].filter((g) => g !== f)),
  };
  // map-relative x, y for the press; page coordinates for moves and releases on the window
  const down = (x: number, y: number) => { for (const f of handlers.mousedown ?? []) f({ lngLat: map.unproject([x, y]) }); };
  const win = (t: string, cx: number, cy: number) => window.dispatchEvent(new MouseEvent(t, { clientX: cx, clientY: cy }));
  return { map, box, down, win };
}

test("drawing a box hides the lens until the box is done, and a click does not finish it", () => {
  const { map, box, down, win } = setup();
  const got: number[][] = [];
  drawBox(map, (b) => got.push(b));
  expect(box.classList.contains("ecd-drawing")).toBe(true);
  down(10, 10); win("mouseup", 110, 60);                         // a click: keep waiting
  expect(got).toEqual([]);
  down(10, 10); win("mousemove", 150, 80); win("mouseup", 130, 70);
  expect(got).toEqual([[1, -2, 3, -1]]);
  expect(box.classList.contains("ecd-drawing")).toBe(false);
});

test("letting go over a map button, the panel or past the edge still finishes the box, clamped to the map", () => {
  const { map, down, win } = setup();
  const got: number[][] = [];
  drawBox(map, (b) => got.push(b));
  down(100, 100);
  win("mouseup", 900, 600);                                      // far right and below the map
  expect(got).toEqual([[10, -30, 40, -10]]);                     // corner clamped to (400, 300)
});

function layerMap() {
  const order: string[] = [], sources = new Set<string>(), handlers: Record<string, (() => void)[]> = {};
  const map: any = {
    style: { _loaded: true },
    getSource: (id: string) => (sources.has(id) ? { setData() {} } : undefined), addSource: (id: string) => sources.add(id),
    getLayer: (id: string) => order.includes(id), addLayer: (l: { id: string }) => order.push(l.id),
    getLayersOrder: () => [...order], moves: 0,
    moveLayer: (id: string, before?: string) => {
      map.moves++;
      order.splice(order.indexOf(id), 1);
      order.splice(before ? order.indexOf(before) : order.length, 0, id);
    },
    on: (ev: string, f: () => void) => (handlers[ev] ??= []).push(f),
    off: (ev: string, f: () => void) => (handlers[ev] = handlers[ev].filter((g) => g !== f)),
    fire: (ev: string) => (handlers[ev] ?? []).forEach((f) => f()),
  };
  return { map, order };
}

test("the area outline goes back on top each time it is shown, under GeoLibre's highlight", () => {
  const { map, order } = layerMap();
  order.push("geolibre-highlight-fill");
  showArea(map, [1, 2, 3, 4]);
  order.unshift("sentinel-2024");
  order.splice(order.indexOf("geolibre-highlight-fill"), 0, "ecd-run-change-0");   // a run, added on top
  showArea(map, [1, 2, 3, 4]);
  expect(order).toEqual(["sentinel-2024", "ecd-run-change-0", "ecd-area-line", "geolibre-highlight-fill"]);
});

test("after GeoLibre re-places its layers over the outline, the outline is raised again on the next frame, once", async () => {
  const { map, order } = layerMap();
  const stop = keepAreaOnTop(map);
  showArea(map, [1, 2, 3, 4]);
  order.push("sentinel-2024", "ecd-run-change-0");                 // GeoLibre's sync, after ours
  map.moves = 0;
  map.fire("styledata"); map.fire("styledata");
  await new Promise((r) => requestAnimationFrame(r));
  expect(order.at(-1)).toBe("ecd-area-line");
  expect(map.moves).toBe(1);
  map.fire("styledata");                                            // our own move: already on top
  await new Promise((r) => requestAnimationFrame(r));
  expect(map.moves).toBe(1);
  stop();
  order.push("later");
  map.fire("styledata");
  await new Promise((r) => requestAnimationFrame(r));
  expect(order.at(-1)).toBe("later");
});
