import { drawBox } from "../src/map/drawbox";

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
