import { drawBox } from "../src/map/drawbox";

test("drawing a box hides the lens until the box is done, and a click does not finish it", () => {
  const handlers: Record<string, ((e: any) => void)[]> = {};
  const box = document.createElement("div");
  let latlng = { lng: 0, lat: 0 };
  const map: any = {
    style: { _loaded: false }, getCanvas: () => ({ style: {} }), getContainer: () => box,
    dragPan: { disable() {}, enable() {} }, boxZoom: { disable() {}, enable() {} },
    on: (ev: string, f: any) => (handlers[ev] ??= []).push(f),
    off: (ev: string, f: any) => (handlers[ev] = handlers[ev].filter((g) => g !== f)),
  };
  const fire = (ev: string, lng: number, lat: number) => { latlng = { lng, lat }; for (const f of handlers[ev] ?? []) f({ lngLat: latlng }); };
  const got: number[][] = [];
  drawBox(map, (b) => got.push(b));
  expect(box.classList.contains("ecd-drawing")).toBe(true);
  fire("mousedown", 1, 1); fire("mouseup", 1, 1);                 // a click: keep waiting
  expect(got).toEqual([]);
  fire("mousedown", 1, 1); fire("mousemove", 2, 2); fire("mouseup", 3, 2);
  expect(got).toEqual([[1, 1, 3, 2]]);
  expect(box.classList.contains("ecd-drawing")).toBe(false);
});
