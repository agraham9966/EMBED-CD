import { saveBinary, saveText } from "../src/export";

afterEach(() => {
  delete (window as any).showSaveFilePicker;
  vi.restoreAllMocks();
});

test("picker is used when available", async () => {
  const write = vi.fn(), close = vi.fn();
  (window as any).showSaveFilePicker = vi.fn(async () => ({ createWritable: async () => ({ write, close }) }));
  expect(await saveBinary("a.tif", new Blob(["x"]), { "image/tiff": [".tif"] })).toEqual({ method: "picker" });
  expect(write).toHaveBeenCalled();
  expect(close).toHaveBeenCalled();
});

test("cancelling the picker is not an error", async () => {
  (window as any).showSaveFilePicker = vi.fn(async () => { throw new DOMException("no", "AbortError"); });
  expect(await saveBinary("a.tif", new Blob(["x"]), {})).toEqual({ method: "cancelled" });
});

test("falls back to a download link", async () => {
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  expect(await saveBinary("a.tif", new Blob(["x"]), {})).toEqual({ method: "download" });
  expect(click).toHaveBeenCalled();
});

test("text goes through the host when it can", () => {
  const app = { exportTextFile: vi.fn() } as any;
  expect(saveText(app, "a.geojson", "{}")).toEqual({ method: "host" });
  expect(app.exportTextFile).toHaveBeenCalledWith("a.geojson", "{}", undefined);
});
