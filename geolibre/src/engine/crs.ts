// Coordinate systems: only WGS84, Web Mercator and the UTM zones are ever needed.
import proj4 from "proj4";

export type Bbox = [number, number, number, number];   // west, south, east, north

const known = new Set<string>();

/** Make an EPSG code usable by proj4 (UTM zones are defined on demand). */
export function ensureCrs(code: string): string {
  if (known.has(code)) return code;
  const m = /^EPSG:(32[67])(\d\d)$/.exec(code);
  if (m) {
    proj4.defs(code, `+proj=utm +zone=${Number(m[2])}${m[1] === "327" ? " +south" : ""} +datum=WGS84 +units=m +no_defs`);
  } else if (code !== "EPSG:4326" && code !== "EPSG:3857") {
    throw new Error(`unsupported CRS ${code}`);
  }
  known.add(code);
  return code;
}

export function transformer(src: string, dst: string): (x: number, y: number) => [number, number] {
  const t = proj4(ensureCrs(src), ensureCrs(dst));
  return (x, y) => t.forward([x, y]) as [number, number];
}

/** The box around a reprojected box, sampling `densify` extra points per edge like GDAL's
 *  TransformBounds: a straight edge in one CRS is a curve in another. */
export function transformBounds(src: string, dst: string, b: Bbox, densify = 21): Bbox {
  const f = transformer(src, dst);
  const [w, s, e, n] = b;
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  const steps = densify + 1;
  const add = (x: number, y: number) => {
    const [X, Y] = f(x, y);
    if (!Number.isFinite(X) || !Number.isFinite(Y)) return;
    xmin = Math.min(xmin, X); xmax = Math.max(xmax, X);
    ymin = Math.min(ymin, Y); ymax = Math.max(ymax, Y);
  };
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    add(w + (e - w) * t, s); add(w + (e - w) * t, n);
    add(w, s + (n - s) * t); add(e, s + (n - s) * t);
  }
  return [xmin, ymin, xmax, ymax];
}

/** The UTM zone of the box's centre (dock.py:_utm_crs); Web Mercator beyond 84 degrees. */
export function utmCrs(b: Bbox): string {
  const lon = (b[0] + b[2]) / 2, lat = (b[1] + b[3]) / 2;
  if (Math.abs(lat) > 84) return "EPSG:3857";
  const zone = Math.min(60, Math.max(1, Math.floor((lon + 180) / 6) + 1));
  return `EPSG:${lat >= 0 ? 326 : 327}${String(zone).padStart(2, "0")}`;
}
