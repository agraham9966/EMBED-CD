// Which AlphaEarth COG covers what, and how an area is cut into block-aligned tiles.
// Port of embed_cd/source.py: Index.covering and AlphaEarthSource.list_tiles/_tiles_for.
import { readNpz, npyString, type NpyArray } from "./npz";
import { transformBounds, type Bbox } from "./crs";

export const BASE_URL = "https://data.source.coop/tge-labs/aef/v1/annual/";
export const INDEX_NPZ_URL = "https://agraham9966.github.io/EMBED-CD/downloads/aef_index.npz";
export const TILE_PX = 1024;          // = the COG block size: aligned reads are the fast ones
export const NATIVE_RES = 10;

export type IndexRow = {
  url: string; year: number; crs: string;
  utmWest: number; utmSouth: number; utmEast: number; utmNorth: number;
  wgs84West: number; wgs84South: number; wgs84East: number; wgs84North: number;
};

/** A tile is year-independent: bounds in a UTM CRS, on the source's own grid. */
export type Tile = { crs: string; west: number; south: number; east: number; north: number };

export class TileIndex {
  private constructor(private z: Record<string, NpyArray>, private base: string) {}

  static async load(
    fetcher: (url: string) => Promise<ArrayBuffer> = async (u) => (await fetch(u)).arrayBuffer(),
    base = BASE_URL,
  ): Promise<TileIndex> {
    return new TileIndex(await readNpz(await fetcher(INDEX_NPZ_URL)), base);
  }

  private col(name: string): Float64Array | Int32Array {
    return this.z[name].data as Float64Array | Int32Array;
  }

  get length(): number {
    return this.col("year").length;
  }

  row(i: number): IndexRow {
    const c = (n: string) => this.col(n)[i];
    return {
      url: this.base + npyString(this.z.key, i), year: c("year"), crs: npyString(this.z.crs, i),
      utmWest: c("utm_west"), utmSouth: c("utm_south"), utmEast: c("utm_east"), utmNorth: c("utm_north"),
      wgs84West: c("wgs84_west"), wgs84South: c("wgs84_south"),
      wgs84East: c("wgs84_east"), wgs84North: c("wgs84_north"),
    };
  }

  years(): number[] {
    return [...new Set(Array.from(this.col("year")))].sort((a, b) => a - b);
  }

  /** Rows whose footprint overlaps a lon/lat box, for one year. */
  covering([w, s, e, n]: Bbox, year: number): IndexRow[] {
    const yr = this.col("year"), W = this.col("wgs84_west"), E = this.col("wgs84_east");
    const S = this.col("wgs84_south"), N = this.col("wgs84_north");
    const out: IndexRow[] = [];
    for (let i = 0; i < yr.length; i++) {
      if (yr[i] === year && W[i] < e && E[i] > w && S[i] < n && N[i] > s) out.push(this.row(i));
    }
    return out;
  }

  /** The COG containing a tile in a year, or null when that year has none there. */
  rowFor(tile: Tile, year: number): IndexRow | null {
    for (const r of this.coveringTile(tile, year)) {
      if (r.utmWest <= tile.west && r.utmSouth <= tile.south && r.utmEast >= tile.east && r.utmNorth >= tile.north) {
        return r;
      }
    }
    return null;
  }

  // Memoized per (year, zone), as in Python: a job asks twice per tile and the index is ~300k rows.
  private byZone = new Map<string, IndexRow[]>();

  private coveringTile(tile: Tile, year: number): IndexRow[] {
    const k = `${year}|${tile.crs}`;
    let rows = this.byZone.get(k);
    if (!rows) {
      const yr = this.col("year");
      rows = [];
      for (let i = 0; i < yr.length; i++) {
        if (yr[i] === year && npyString(this.z.crs, i) === tile.crs) rows.push(this.row(i));
      }
      this.byZone.set(k, rows);
    }
    return rows;
  }
}

const key = (t: Tile) => `${t.crs}|${t.west}|${t.south}|${t.east}|${t.north}`;

/** Python sorts these namedtuples field by field: crs as a string, then the numbers. */
function compareTiles(a: Tile, b: Tile): number {
  if (a.crs !== b.crs) return a.crs < b.crs ? -1 : 1;
  return a.west - b.west || a.south - b.south || a.east - b.east || a.north - b.north;
}

function tilesFor(index: TileIndex, bbox: Bbox, year: number, factor: number, tilePx: number): Map<string, Tile> {
  const out = new Map<string, Tile>();
  const step = tilePx * NATIVE_RES * factor;
  for (const r of index.covering(bbox, year)) {
    let [left, bottom, right, top] = transformBounds("EPSG:4326", r.crs, bbox);
    const x0 = r.utmWest, y0 = r.utmSouth;
    left = Math.max(left, x0); bottom = Math.max(bottom, y0);
    right = Math.min(right, r.utmEast); top = Math.min(top, r.utmNorth);
    if (right <= left || top <= bottom) continue;
    const c0 = Math.floor((left - x0) / step), c1 = Math.ceil((right - x0) / step);
    const r0 = Math.floor((bottom - y0) / step), r1 = Math.ceil((top - y0) / step);
    for (let cy = r0; cy < r1; cy++) {
      for (let cx = c0; cx < c1; cx++) {
        const w = x0 + cx * step, s = y0 + cy * step;
        const t = { crs: r.crs, west: w, south: s, east: Math.min(w + step, r.utmEast), north: Math.min(s + step, r.utmNorth) };
        out.set(key(t), t);
      }
    }
  }
  return out;
}

/** (all, both, partial) block-aligned tiles over a box. A tile in only one year is still
 *  processed, so the coverage layer can say which year is missing. */
export function listTiles(index: TileIndex, bbox: Bbox, yearA: number, yearB: number, factor: number, tilePx = TILE_PX) {
  const a = tilesFor(index, bbox, yearA, factor, tilePx);
  const b = tilesFor(index, bbox, yearB, factor, tilePx);
  const all = new Map([...a, ...b]);
  const sort = (m: Iterable<Tile>) => [...m].sort(compareTiles);
  return {
    all: sort(all.values()),
    both: sort([...a].filter(([k]) => b.has(k)).map(([, t]) => t)),
    partial: sort([...all].filter(([k]) => a.has(k) !== b.has(k)).map(([, t]) => t)),
  };
}
