// Windowed reads of AlphaEarth COGs. Port of embed_cd/source.py (factor_for, fetch).
// The tile-table preload is from GeoLibre's satellite-embeddings-aef.ts (MIT, opengeos).
import type { GeoTIFF, GeoTIFFImage } from "geotiff";
import { NATIVE_RES, type IndexRow, type Tile } from "./tileindex";

export const MAX_FACTOR = 16;     // 160 m: one whole embedding cell per source pixel
export const NODATA_RAW = -128;

/** Raw int8 embeddings, pixel-interleaved (64 values per pixel), north-up. 64 bytes a pixel. */
export type Cube = { data: Int8Array; height: number; width: number; bands: 64 };
/** North-up, square pixels: top-left corner and pixel size. */
export type Affine = { x0: number; y0: number; res: number };

/** The coarsest power-of-two overview still at least as fine as the requested resolution. */
export function factorFor(resM: number): number {
  let f = 1;
  while (f * 2 <= MAX_FACTOR && NATIVE_RES * f * 2 <= resM) f *= 2;
  return f;
}

// geotiff.js otherwise asks for each block's offset and size with its own tiny range request.
const tablesLoaded = new WeakMap<GeoTIFFImage, Promise<unknown>>();
function loadTileTables(img: GeoTIFFImage): Promise<unknown> {
  let p = tablesLoaded.get(img);
  if (!p) {
    const dir = (img as any).fileDirectory;
    p = dir?.loadValue
      ? Promise.all([dir.loadValue("TileOffsets"), dir.loadValue("TileByteCounts")])
      : Promise.resolve();
    tablesLoaded.set(img, p);
  }
  return p;
}

/** A tile's cube for one year, or null when the window falls outside the file. The COGs are
 *  BOTTOM-UP (row 0 is the south edge), so the window is counted northward, then flipped. */
export async function fetchTile(
  row: IndexRow,
  tile: Tile,
  factor: number,
  open: (url: string) => Promise<GeoTIFF>,
  signal?: AbortSignal,
): Promise<{ cube: Cube; crs: string; transform: Affine } | null> {
  const tiff = await open(row.url);
  const img = await tiff.getImage(Math.log2(factor));
  const res = NATIVE_RES * factor;
  const colOff = Math.round((tile.west - row.utmWest) / res);
  const rowOff = Math.round((tile.south - row.utmSouth) / res);       // rows count NORTHWARD
  const width = Math.min(Math.round((tile.east - tile.west) / res), img.getWidth() - colOff);
  const height = Math.min(Math.round((tile.north - tile.south) / res), img.getHeight() - rowOff);
  if (width <= 0 || height <= 0) return null;
  await loadTileTables(img);
  const raw = (await img.readRasters({
    window: [colOff, rowOff, colOff + width, rowOff + height],
    interleave: true,
    signal,
  })) as unknown as Int8Array;
  // GDAL < 3.7 needed a uint8 -> int8 fix here; geotiff.js honours SampleFormat, but make sure.
  const src = raw instanceof Int8Array ? raw : new Int8Array(raw.buffer, raw.byteOffset, raw.length);
  const rowBytes = width * 64;
  const data = new Int8Array(height * rowBytes);
  for (let r = 0; r < height; r++) {
    data.set(src.subarray((height - 1 - r) * rowBytes, (height - r) * rowBytes), r * rowBytes);
  }
  return {
    cube: { data, height, width, bands: 64 },
    crs: row.crs,
    transform: { x0: tile.west, y0: tile.south + height * res, res },
  };
}
