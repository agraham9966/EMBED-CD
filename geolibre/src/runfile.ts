// A saved run is ONE file the user puts where they like: a zip of run.json plus the change map
// and its coverage as GeoTIFFs (they open in QGIS as they are). The GeoLibre counterpart of the
// QGIS plugin's "Save to" folder.
import { writeArrayBuffer } from "geotiff";
import { fromArrayBuffer } from "geotiff";
import type { Grid } from "./engine/grid";
import { mosaic, type TileResult } from "./engine/job";
import { readZip } from "./engine/npz";
import { histogram, NODATA } from "./engine/score";
import type { RunRecord } from "./runs";

const BLOCK = 1024;           // reopened maps are cut back into display tiles of at most this

export function runFileName(meta: RunRecord, when = new Date(meta.createdAt)): string {
  const d = `${when.getFullYear()}-${String(when.getMonth() + 1).padStart(2, "0")}-${String(when.getDate()).padStart(2, "0")}`;
  return `embed-cd_${meta.yearA}-${meta.yearB}_${meta.resM}m_${d}.zip`;
}

function geotiff(values: Float32Array | Uint8Array, g: Grid, nodata?: number): Uint8Array {
  const epsg = Number(g.crs.split(":")[1]);
  const meta: Record<string, unknown> = {
    width: g.width, height: g.height,
    ModelPixelScale: [g.res, g.res, 0], ModelTiepoint: [0, 0, 0, g.x0, g.y0, 0],
    GTModelTypeGeoKey: 1, GTRasterTypeGeoKey: 1, ProjectedCSTypeGeoKey: epsg,
  };
  if (nodata !== undefined) meta.GDAL_NODATA = `${nodata}\0`;
  return new Uint8Array(writeArrayBuffer(values, meta as any));
}

export async function writeRunFile(meta: RunRecord, tiles: TileResult[]): Promise<Blob> {
  const m = mosaic(meta.grid, tiles);
  return writeZip({
    "run.json": new TextEncoder().encode(JSON.stringify({ format: "embed-cd-run", version: 1, meta }, null, 1)),
    "change.tif": geotiff(m.score, meta.grid, NODATA),
    "coverage.tif": geotiff(m.cov, meta.grid),
  });
}

export async function readRunFile(ab: ArrayBuffer): Promise<{ meta: RunRecord; tiles: TileResult[] }> {
  const files = await readZip(ab);
  const doc = files["run.json"] && JSON.parse(new TextDecoder().decode(files["run.json"]));
  if (doc?.format !== "embed-cd-run") throw new Error("This is not an EMBED-CD run file.");
  const meta = doc.meta as RunRecord;
  const band = async (name: string) => {
    const img = await (await fromArrayBuffer(files[name].slice().buffer)).getImage();
    return (await img.readRasters({ interleave: true })) as unknown as Float32Array | Uint8Array;
  };
  const score = (await band("change.tif")) as Float32Array, cov = (await band("coverage.tif")) as Uint8Array;
  const { width: W, height: H } = meta.grid, tiles: TileResult[] = [];
  for (let r0 = 0; r0 < H; r0 += BLOCK) {
    for (let c0 = 0; c0 < W; c0 += BLOCK) {
      const r1 = Math.min(H, r0 + BLOCK), c1 = Math.min(W, c0 + BLOCK), w = c1 - c0;
      const s = new Float32Array((r1 - r0) * w), c = new Uint8Array((r1 - r0) * w);
      for (let y = r0; y < r1; y++) {
        s.set(score.subarray(y * W + c0, y * W + c1), (y - r0) * w);
        c.set(cov.subarray(y * W + c0, y * W + c1), (y - r0) * w);
      }
      tiles.push({ window: [r0, r1, c0, c1], score: s, cov: c, hist: histogram(s) });
    }
  }
  return { meta, tiles };
}

// ---- a minimal zip writer (deflate through the browser's CompressionStream) ------------------
const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c;
});
function crc32(b: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

async function deflateRaw(b: Uint8Array): Promise<Uint8Array> {
  const input = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(b); c.close(); } });
  const out = input.pipeThrough(new CompressionStream("deflate-raw") as unknown as ReadableWritablePair<Uint8Array, Uint8Array>);
  return new Uint8Array(await new Response(out).arrayBuffer());
}

async function writeZip(files: Record<string, Uint8Array>): Promise<Blob> {
  const parts: BlobPart[] = [], central: Uint8Array[] = [];
  let offset = 0;
  for (const [name, data] of Object.entries(files)) {
    const nameBytes = new TextEncoder().encode(name);
    const packed = await deflateRaw(data);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
    local.setUint16(8, 8, true); local.setUint32(14, crc, true);
    local.setUint32(18, packed.length, true); local.setUint32(22, data.length, true);
    local.setUint16(26, nameBytes.length, true);
    parts.push(local.buffer, nameBytes as BlobPart, packed as BlobPart);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 20, true); cd.setUint16(6, 20, true);
    cd.setUint16(8, 0x0800, true); cd.setUint16(10, 8, true); cd.setUint32(16, crc, true);
    cd.setUint32(20, packed.length, true); cd.setUint32(24, data.length, true);
    cd.setUint16(28, nameBytes.length, true); cd.setUint32(42, offset, true);
    central.push(new Uint8Array(cd.buffer), nameBytes);
    offset += 30 + nameBytes.length + packed.length;
  }
  const cdSize = central.reduce((n, b) => n + b.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, Object.keys(files).length, true); end.setUint16(10, Object.keys(files).length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...(central as BlobPart[]), end.buffer], { type: "application/zip" });
}
