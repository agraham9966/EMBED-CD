// Read the Python engine's reference outputs (see scripts/make_geolibre_fixtures.py).
import { readFileSync } from "node:fs";
import { join } from "node:path";

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
