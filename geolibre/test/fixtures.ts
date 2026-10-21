// Read the Python engine's reference outputs (see scripts/make_geolibre_fixtures.py).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fromArrayBuffer } from "geotiff";
import type { IndexRow } from "../src/engine/tileindex";

const DIR = join(import.meta.dirname, "fixtures");

export const fx = (name: string): Buffer => readFileSync(join(DIR, name));
export const fxJson = (name: string): any => JSON.parse(readFileSync(join(DIR, name), "utf8"));
export const fxBuffer = (name: string): ArrayBuffer => {
  const b = fx(name);
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer;
};
export const f32 = (name: string) => new Float32Array(fxBuffer(name));
export const u8 = (name: string) => new Uint8Array(fxBuffer(name));
export const i8 = (name: string) => new Int8Array(fxBuffer(name));

/** An index row for one of the synthetic fixture COGs (see fetch.json). */
export function fixtureRow(file: string, year: number): IndexRow {
  const F = fxJson("fetch.json");
  return {
    url: file, year, crs: "EPSG:32610",
    utmWest: F.cog_origin[0], utmSouth: F.cog_origin[1],
    utmEast: F.cog_origin[0] + F.npx * 10, utmNorth: F.cog_origin[1] + F.npx * 10,
    wgs84West: 0, wgs84South: 0, wgs84East: 0, wgs84North: 0,
  };
}
export const openFixture = async (url: string) => fromArrayBuffer(fxBuffer(url));
