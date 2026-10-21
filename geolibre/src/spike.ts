// TEMPORARY (removed in chunk 6): the "Storage check" that proves, on a real GeoLibre install,
// that runs survive a restart and that a binary GeoTIFF can be exported.
import { writeArrayBuffer } from "geotiff";
import type { GeoLibreAppAPI } from "./host-api";
import { openRunStore, describeStorageError, type RunStore } from "./storage";
import { saveBinary, saveText } from "./export";

const RUN_ID = "storage-check";
const TEST_BYTES = 20 * 1024 * 1024;

/** 64x64 Float32, values row/63, 10 m pixels in UTM 10N with its corner at 500000, 5500000. */
export function makeTestGeoTiff(): Blob {
  const values = new Float32Array(64 * 64);
  for (let r = 0; r < 64; r++) values.fill(r / 63, r * 64, (r + 1) * 64);
  const buf = writeArrayBuffer(values, {
    width: 64,
    height: 64,
    ModelPixelScale: [10, 10, 0],
    ModelTiepoint: [0, 0, 0, 500000, 5500000, 0],
    GTModelTypeGeoKey: 1,
    GTRasterTypeGeoKey: 1,
    ProjectedCSTypeGeoKey: 32610,
  } as any);
  return new Blob([buf], { type: "image/tiff" });
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, text = "") {
  const e = document.createElement(tag);
  e.className = cls;
  e.textContent = text;
  return e;
}

export function renderSpike(container: HTMLElement, app: GeoLibreAppAPI): () => void {
  const box = el("section", "ecd-spike");
  box.append(el("h3", "ecd-h", "Storage check (temporary)"));
  const status = el("p", "ecd-status", "Checking for a saved test run...");
  const saveBtn = el("button", "ecd-btn", "Save test run");
  const tifBtn = el("button", "ecd-btn", "Export test GeoTIFF");
  const jsonBtn = el("button", "ecd-btn", "Export test GeoJSON");
  const out = el("p", "ecd-status", "");
  box.append(status, saveBtn, tifBtn, jsonBtn, out);
  container.append(box);

  let store: RunStore | null = null;
  const ready = openRunStore()
    .then(async (s) => {
      store = s;
      const found = (await s.listRuns()).find((r) => r.id === RUN_ID);
      status.textContent = found
        ? `Found the test run saved on ${new Date(found.createdAt).toLocaleString()}. Storage survives a restart.`
        : 'No test run yet. Press "Save test run", then restart GeoLibre and open this panel again.';
    })
    .catch((e) => {
      status.textContent = describeStorageError(e);
      saveBtn.disabled = true;
    });

  saveBtn.onclick = async () => {
    await ready;
    if (!store) return;
    out.textContent = "Saving 20 MB...";
    try {
      const bytes = new Uint8Array(TEST_BYTES);
      for (let i = 0; i < bytes.length; i++) bytes[i] = i % 251;
      await store.putRun({ id: RUN_ID, name: "Storage check", createdAt: Date.now(), bytes: TEST_BYTES });
      await store.putBlob(RUN_ID, "data.bin", new Blob([bytes]));
      const back = new Uint8Array(await (await store.getBlob(RUN_ID, "data.bin"))!.arrayBuffer());
      const ok = back.length === TEST_BYTES && back[0] === 0 && back[TEST_BYTES - 1] === (TEST_BYTES - 1) % 251;
      await store.persist();
      const use = await store.usage();
      out.textContent = ok
        ? "Saved 20 MB and read it back." +
          (use ? ` Using ${(use.used / 1e6).toFixed(0)} MB of ${(use.quota / 1e6).toFixed(0)} MB.` : "")
        : "Saved, but the data read back did not match.";
    } catch (e) {
      out.textContent = describeStorageError(e);
    }
  };

  tifBtn.onclick = async () => {
    try {
      const r = await saveBinary("embed-cd-test.tif", makeTestGeoTiff(), { "image/tiff": [".tif"] });
      out.textContent = {
        picker: "Saved. Open it in QGIS: it should sit in UTM 10N near Vancouver Island.",
        download: "Sent as a download. Check your Downloads folder for embed-cd-test.tif.",
        host: "Saved.",
        cancelled: "Cancelled.",
      }[r.method];
    } catch (e) {
      out.textContent = `Export failed: ${(e as Error)?.message ?? e}`;
    }
  };

  jsonBtn.onclick = () => {
    const fc = {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: { name: "EMBED-CD test" },
                   geometry: { type: "Point", coordinates: [-125.29, 49.67] } }],
    };
    const r = saveText(app, "embed-cd-test.geojson", JSON.stringify(fc),
                       { description: "GeoJSON", extensions: ["geojson"], mimeType: "application/geo+json" });
    out.textContent = r.method === "host" ? "Sent to the save dialog." : "Sent as a download.";
  };

  return () => box.remove();
}
