import { estimateText, progressText, sizeWarning } from "../src/ui/step1";

test("the estimate reads like QGIS", () => {
  expect(estimateText({ tiles: 4, gb: 0.536, px: 1_000_000 }, { width: 1000, height: 1000 }))
    .toBe("4 tiles, about 0.5 GB to read, output 1000 x 1000 px");
});

test("a very large map warns but does not block", () => {
  expect(sizeWarning(60_000_000, "10 m (full)")).toBe("");
  expect(sizeWarning(75_400_000, "10 m (full)")).toBe(
    "This area at 10 m (full) makes a very large map (75 million pixels) and may run out of browser memory. A coarser Detail is safer.");
});

test("progress reads busy, then counts tiles, then reports failures", () => {
  expect(progressText({ type: "plan", tiles: 3 } as any)).toBe("Starting: reading the first tile (the slowest one)...");
  expect(progressText({ type: "tile", done: 1, total: 3 } as any)).toBe("1 of 3 tiles");
  expect(progressText({ type: "end", failed: 1, cancelled: false } as any)).toBe("Done. 1 tile(s) could not be read and are left out.");
  expect(progressText({ type: "end", failed: 0, cancelled: true } as any)).toBe("Cancelled. The finished tiles are kept.");
  expect(progressText({ type: "end", failed: 0, cancelled: false } as any)).toBe("Done.");
});

test("the area outline is skipped while the style is loading, not an error", async () => {
  const { showArea } = await import("../src/map/drawbox");
  const map: any = { style: { _loaded: false }, getSource: () => undefined, addSource: () => { throw new Error("Style is not done loading"); } };
  expect(() => showArea(map, [-125.3, 49.6, -125.2, 49.7])).not.toThrow();
});
