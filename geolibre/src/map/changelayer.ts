// The change map and its data-coverage layer on the MapLibre map. Each tile is a canvas placed by
// its four corners; overlapping tiles are drawn through the mosaic rules (engine/job compose), so
// where two tiles overlap they show the same pixels. Colours copy the QGIS plugin (dock.py).
import { transformBounds, transformer, type Bbox } from "../engine/crs";
import type { Grid, Window } from "../engine/grid";
import { compose, type TileResult } from "../engine/job";
import { NODATA } from "../engine/score";
import type { GeoLibreAppAPI } from "../host-api";

export const CHANGE_OPACITY = 0.85;
type RGBA = [number, number, number, number];
const TOP = 0.5;                                  // the ramp's top colour from this score up
// "No data in either year" (mostly open sea) is a translucent grey, not near-white: white vanished
// on GeoLibre's light default basemap and glared on imagery.
const COVERAGE: RGBA[] = [[45, 50, 60, 60], [0, 0, 0, 0], [214, 60, 200, 220], [60, 170, 235, 220], [110, 115, 125, 170]];

/** Google's Turbo colormap (Mikhailov 2019, polynomial fit), t in 0..1: the ramp QGIS ships as
 *  "Turbo". Dark blue, cyan, green, yellow, orange, dark red: every step visible on light basemaps
 *  and on imagery alike, where the old cyan-yellow-red washed out on light ground. */
export function turbo(t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, t));
  const r = 0.13572138 + x * (4.6153926 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))));
  const g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))));
  const b = 0.1066733 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))));
  return [r, g, b].map((v) => Math.round(255 * Math.min(1, Math.max(0, v)))) as [number, number, number];
}

/** Turbo over 0..TOP, fixed: a pixel keeps its colour as the cutoff moves, and red means the same
 *  score in every run. Starts a tenth of the way in, where the fit is blue rather than near-black. */
function ramp(v: number): RGBA {
  return [...turbo(0.1 + 0.9 * Math.min(Math.max(v, 0) / TOP, 1)), 255];
}

/** Transparent at or below the cutoff and for no data. */
export function scoreColour(v: number, cutoff: number): RGBA {
  if (v === NODATA || !(v > cutoff)) return [0, 0, 0, 0];
  return ramp(v);
}

export const coverageColour = (code: number): RGBA => COVERAGE[code] ?? [0, 0, 0, 0];

/** RGBA as one little-endian 32-bit pixel, for writing an ImageData through a Uint32Array. */
const pack = ([r, g, b, a]: RGBA) => (r | (g << 8) | (b << 16) | (a << 24)) >>> 0;
// The ramp in steps of 0.001 (indistinguishable on screen); everything past TOP is the top colour.
const LUT_STEPS = 1000 * TOP;
const SCORE_LUT = Uint32Array.from({ length: LUT_STEPS + 1 }, (_, i) => pack(ramp(i / 1000)));
const COVERAGE_PX = COVERAGE.map(pack);

/** The window's corners in lon/lat, in MapLibre's order: top-left, top-right, bottom-right, bottom-left. */
export function tileCorners(g: Grid, [r0, r1, c0, c1]: Window): [number, number][] {
  const f = transformer(g.crs, "EPSG:4326");
  const x = (c: number) => g.x0 + c * g.res, y = (r: number) => g.y0 - r * g.res;
  return [f(x(c0), y(r0)), f(x(c1), y(r0)), f(x(c1), y(r1)), f(x(c0), y(r1))];
}

/** MapLibre stretches each canvas between its four corners in web-map space, where the grid's UTM
 *  lines are slightly curved, so a big tile drifts off its true place between the corners: half a
 *  pixel at 75 m Detail, four at 160 m (the objects, drawn point by point, do not). The drift
 *  grows with the square of the tile's size, so split a window k x k until it is under a third of
 *  a pixel. 10 m tiles are never split. */
export function splitWindow(g: Grid, w: Window): Window[] {
  const [r0, r1, c0, c1] = w;
  const f = transformer(g.crs, "EPSG:4326");
  // Web Mercator in metres at the tile's latitude (one scale for the whole tile).
  const R = 6378137 * Math.cos((f(g.x0 + ((c0 + c1) / 2) * g.res, g.y0 - ((r0 + r1) / 2) * g.res)[1] * Math.PI) / 180);
  const at = (r: number, c: number) => {
    const [lon, lat] = f(g.x0 + c * g.res, g.y0 - r * g.res);
    return [(R * lon * Math.PI) / 180, R * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))];
  };
  const C = [at(r0, c0), at(r0, c1), at(r1, c1), at(r1, c0)];
  let drift = 0;
  for (const u of [0, 0.25, 0.5, 0.75, 1]) {
    for (const v of [0, 0.25, 0.5, 0.75, 1]) {
      const flat = [0, 1].map((q) => (1 - u) * (1 - v) * C[0][q] + u * (1 - v) * C[1][q] + u * v * C[2][q] + (1 - u) * v * C[3][q]);
      const [x, y] = at(r0 + v * (r1 - r0), c0 + u * (c1 - c0));
      drift = Math.max(drift, Math.hypot(flat[0] - x, flat[1] - y));
    }
  }
  const k = Math.max(1, Math.ceil(Math.sqrt(drift / (g.res / 3))));
  const cut = (a: number, b: number, i: number) => a + Math.round(((b - a) * i) / k);
  const out: Window[] = [];
  for (let i = 0; i < k; i++) {
    for (let j = 0; j < k; j++) {
      const s: Window = [cut(r0, r1, i), cut(r0, r1, i + 1), cut(c0, c1, j), cut(c0, c1, j + 1)];
      if (s[1] > s[0] && s[3] > s[2]) out.push(s);
    }
  }
  return out;
}

type Drawn = {
  result: TileResult;
  /** The piece's composed scores as codes, ceil(score * 10000) (0 for no data): half the size of
   *  float32, and `code > cutoff * 10000` is exactly `score > cutoff` on the slider's steps. */
  codes: Uint16Array;
  /** What the canvas covers: the tile's window plus one pixel right and down (see addTile). */
  win: Window;
  canvas: HTMLCanvasElement; covCanvas: HTMLCanvasElement; coordinates: [number, number][];

};

/** Stretch the image evenly between its corners. MapLibre 6 otherwise reads a quad that is a hair
 *  wider at the top as a picture seen in perspective, and squeezes its middle: a UTM tile is just
 *  such a quad in web-map space, and the change map sat a pixel off the objects. */
const flat = (map: any, id: string) => map.getSource(id)?.setWarp?.("flat");

/** One image buffer shared by every canvas (each piece used to keep its own, ~2.7 MB a tile at
 *  50 m): grown to the largest piece, drawn from with putImageData's dirty rectangle. */
let shared: { img: ImageData; px: Uint32Array } | null = null;
function scratch(ctx: CanvasRenderingContext2D, w: number, h: number): [ImageData, Uint32Array] {
  if (!shared || shared.img.width < w || shared.img.height < h) {
    const img = ctx.createImageData(Math.max(w, shared?.img.width ?? 0), Math.max(h, shared?.img.height ?? 0));
    shared = { img, px: new Uint32Array(img.data.buffer) };
  }
  return [shared.img, shared.px];
}

/** The style has its JSON (sources and layers can be added). */
export const styleReady = (map: any): boolean => !!map?.style?._loaded;

/** The user removed layer `id` in the Layers panel (false where GeoLibre cannot say). */
export function removedByUser(app: GeoLibreAppAPI, id: string): boolean {
  const listed = (app as any).listLayers?.() as { id: string }[] | undefined;
  return !!listed && !listed.some((l) => l.id === id);
}

export class ChangeLayer {
  private tiles: Drawn[] = [];
  private cutoff = 0.15;
  private pending = false;
  private visible = { change: true, coverage: true, extent: true };
  private bounds: Bbox | null = null;            // lon/lat, for GeoLibre's Zoom to layer
  private registered = false;
  /** Change or coverage, removed by the user in the Layers panel: never put back by this layer. */
  private dropped = new Set<string>();
  /** The run's Layers-panel group, set before the first tile is registered. */
  groupId?: string;
  readonly changeId: string;
  readonly coverageId: string;
  /** The run's extent as a thin outline: the grid's four corners, in lon/lat. */
  readonly extentId: string;
  private extent: [number, number][] | null = null;

  constructor(
    private app: GeoLibreAppAPI,
    private runId: string,
    private label: string,                       // "2019-2024"
    private schedule: (f: () => void) => void = (f) => requestAnimationFrame(f),
  ) {
    this.changeId = `ecd-${runId}-change`;
    this.coverageId = `ecd-${runId}-coverage`;
    this.extentId = `ecd-${runId}-extent`;
    // GeoLibre swaps the whole style on a basemap change (setStyle, diff: false), which drops
    // every source and layer a plugin added. Put ours back each time.
    this.map?.on?.("style.load", this.onStyle);
  }

  private onStyle = () => {
    this.prune();
    this.tiles.forEach((_, i) => this.addToMap(i));
    this.addExtent();
  };

  /** Notice layers the user removed, and take what is left of them off the map. */
  private prune(): void {
    if (!this.registered) return;
    for (const base of [this.changeId, this.coverageId, this.extentId]) {
      if (this.dropped.has(base) || !removedByUser(this.app, base)) continue;
      this.dropped.add(base);
      if (base === this.extentId) this.removeExtent(); else this.removeNative(base);
    }
  }

  /** All three layers still on the map. */
  get intact(): boolean {
    this.prune();
    return this.dropped.size === 0;
  }

  /** The change map or its coverage still on the map. */
  get shown(): boolean {
    this.prune();
    return !this.dropped.has(this.changeId) || !this.dropped.has(this.coverageId);
  }

  private get map(): any {
    return (this.app as any).getMap?.();
  }

  /** A finished tile: draw it, and redraw any tile it overlaps so overlaps agree. */
  addTile(grid: Grid, result: TileResult): void {
    const map = this.map;
    if (!map) return;
    this.prune();
    this.bounds ??= transformBounds(grid.crs, "EPSG:4326",
      [grid.x0, grid.y0 - grid.height * grid.res, grid.x0 + grid.width * grid.res, grid.y0]);
    if (!this.extent) {
      const c = tileCorners(grid, [0, grid.height, 0, grid.width]);
      this.extent = [...c, c[0]];
      this.addExtent();
    }
    const all = [...this.tiles.map((t) => t.result), result];
    for (const t of this.tiles) {
      if (overlaps(t.win, result.window)) {
        this.paint(t, compose(t.win, all));
        this.refresh(this.tiles.indexOf(t));
      }
    }
    for (const [r0, r1, c0, c1] of splitWindow(grid, result.window)) {
      // One pixel past the piece's own right and bottom edge, filled from the neighbour through
      // the same mosaic rules: two image quads that merely touch can leave a hairline of map
      // showing between them (it showed through the coverage layer as tile outlines).
      const win: Window = [r0, Math.min(r1 + 1, grid.height), c0, Math.min(c1 + 1, grid.width)];
      const canvas = document.createElement("canvas"), covCanvas = document.createElement("canvas");
      canvas.width = covCanvas.width = win[3] - win[2];
      canvas.height = covCanvas.height = win[1] - win[0];
      const d: Drawn = { result, win, codes: new Uint16Array(0), canvas, covCanvas, coordinates: tileCorners(grid, win) };
      this.tiles.push(d);
      this.paint(d, compose(win, all));
      this.addToMap(this.tiles.length - 1);
    }
    // GeoLibre is told on the first tile only; the caller publishes the rest when the run ends.
    // Every registration that changes the layer list makes GeoLibre rebuild its map layer
    // control, which leaks three map listeners each time (maplibre-gl-layer-control 0.18.2);
    // registering per tile made tile n cost ~n times more than tile 1. Visibility and opacity
    // still reach every piece meanwhile, through the paint bridge (it uses this.ids()).
    if (!this.registered) this.register();
  }

  /** Add tile i's sources and layers, unless the style is mid-swap (style.load re-adds them). */
  private addToMap(i: number): void {
    const map = this.map, t = this.tiles[i];
    if (!map || !t || !styleReady(map)) return;
    const { coordinates } = t;
    const cov = `${this.coverageId}-${i}`, chg = `${this.changeId}-${i}`;
    if (!this.dropped.has(this.coverageId) && !map.getSource(cov)) {
      map.addSource(cov, { type: "canvas", canvas: t.covCanvas, coordinates, animate: false });
      flat(map, cov);
      // Coverage goes under every change tile; change tiles stack on top.
      map.addLayer({ id: cov, type: "raster", source: cov,
        layout: { visibility: this.visible.coverage ? "visible" : "none" },
        paint: { "raster-fade-duration": 0, "raster-resampling": "nearest" } },
        map.getLayer(`${this.changeId}-0`) ? `${this.changeId}-0` : undefined);
    }
    if (!this.dropped.has(this.changeId) && !map.getSource(chg)) {
      map.addSource(chg, { type: "canvas", canvas: t.canvas, coordinates, animate: false });
      flat(map, chg);
      map.addLayer({ id: chg, type: "raster", source: chg,
        layout: { visibility: this.visible.change ? "visible" : "none" },
        paint: { "raster-opacity": CHANGE_OPACITY, "raster-fade-duration": 0, "raster-resampling": "nearest" } });
    }
  }

  /** The extent outline: a thin white line on a dark edge, readable on any basemap, no fill. */
  private addExtent(): void {
    const map = this.map, id = this.extentId;
    if (!map || !this.extent || this.dropped.has(id) || !styleReady(map) || map.getSource(id)) return;
    const layout = { visibility: this.visible.extent ? "visible" : "none" };
    map.addSource(id, { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: this.extent } } });
    map.addLayer({ id: `${id}-casing`, type: "line", source: id, layout, paint: { "line-color": "rgba(20,22,28,0.55)", "line-width": 3.5 } });
    map.addLayer({ id: `${id}-line`, type: "line", source: id, layout, paint: { "line-color": "rgba(255,255,255,0.9)", "line-width": 1.5 } });
  }

  private removeExtent(): void {
    const map = this.map, id = this.extentId;
    for (const l of [`${id}-line`, `${id}-casing`]) if (map?.getLayer(l)) map.removeLayer(l);
    if (map?.getSource(id)) map.removeSource(id);
  }

  /** Coalesced to one recolour per frame: dragging the slider never queues work up. */
  setCutoff(t: number): void {
    // A run re-sends the cutoff with every tile; recolouring all of them each time made tile n
    // cost n repaints and texture uploads (a whole-island run crawled after ~40 tiles).
    if (t === this.cutoff) return;
    this.cutoff = t;
    if (this.pending) return;
    this.pending = true;
    this.schedule(() => {
      this.pending = false;
      this.recolourAll();
    });
  }

  /** Only the change canvases: coverage does not depend on the cutoff, so it is not resent. */
  private recolourAll(): void {
    this.tiles.forEach((t, i) => {
      this.paintScore(t);
      this.refresh(i, false);
    });
  }

  /** New or recomposed pixels: keep the scores as codes, draw both canvases. Coverage does not
   *  depend on the cutoff, so it is drawn once and not kept. */
  private paint(t: Drawn, m: { score: Float32Array; cov: Uint8Array }): void {
    const codes = new Uint16Array(m.score.length);
    for (let p = 0; p < codes.length; p++) {
      const v = m.score[p];
      codes[p] = v === NODATA || !(v > 0) ? 0 : Math.min(65535, Math.ceil(v * 10000));
    }
    t.codes = codes;
    this.paintScore(t);
    const ctx = t.covCanvas.getContext("2d");
    if (!ctx) return;
    const w = t.covCanvas.width, h = t.covCanvas.height, [img, px] = scratch(ctx, w, h), W = img.width;
    for (let y = 0, p = 0; y < h; y++) for (let x = 0; x < w; x++, p++) px[y * W + x] = COVERAGE_PX[m.cov[p]] ?? 0;
    ctx.putImageData(img, 0, 0, 0, 0, w, h);
  }

  /** The slider's whole cost: one table lookup per pixel, into the shared scratch image. */
  private paintScore(t: Drawn): void {
    const ctx = t.canvas.getContext("2d");
    if (!ctx) return;
    const w = t.canvas.width, h = t.canvas.height, [img, px] = scratch(ctx, w, h), W = img.width;
    const q = t.codes, lut = SCORE_LUT, c = this.cutoff * 10000;
    for (let y = 0, p = 0; y < h; y++) {
      const row = y * W;
      for (let x = 0; x < w; x++, p++) {
        const v = q[p];
        px[row + x] = v > c ? lut[Math.min(LUT_STEPS, Math.round(v / 10))] : 0;
      }
    }
    ctx.putImageData(img, 0, 0, 0, 0, w, h);
  }

  /** A canvas source re-uploads only while playing: play for one frame, then pause. */
  private refresh(i: number, coverage = true): void {
    const map = this.map;
    for (const id of coverage ? [`${this.changeId}-${i}`, `${this.coverageId}-${i}`] : [`${this.changeId}-${i}`]) {
      const src = map?.getSource(id);
      if (!src) continue;
      src.play?.();
      map.once("render", () => src.pause?.());
    }
    map?.triggerRepaint?.();
  }

  private ids(base: string): string[] {
    return this.tiles.map((_, i) => `${base}-${i}`);
  }

  /** Tell GeoLibre about every piece drawn so far: at the end of a run, and after a saved run's
   *  tiles are loaded. */
  publish(): void {
    if (this.tiles.length) this.register();
  }

  private register(): void {
    const reg = this.app.registerExternalNativeLayer;
    if (!reg) return;
    for (const [id, name, key, opacity] of [
      [this.changeId, `Change ${this.label}`, "change", CHANGE_OPACITY],
      [this.coverageId, `Data coverage ${this.label}`, "coverage", 1],
    ] as const) {
      if (this.dropped.has(id)) continue;
      reg.call(this.app, {
        id, name, type: "raster", nativeLayerIds: this.ids(id), sourceIds: this.ids(id), opacity,
        metadata: this.bounds ? { bounds: this.bounds } : {}, groupId: this.groupId,
        paintMode: "plugin",
        paintBridge: {
          setOpacity: (o: number) => this.ids(id).forEach((l) => this.map?.setPaintProperty(l, "raster-opacity", o)),
          setVisibility: (v: boolean) => {
            this.visible[key] = v;
            this.ids(id).forEach((l) => this.map?.setLayoutProperty(l, "visibility", v ? "visible" : "none"));
          },
        },
      } as any);
    }
    const ext = [`${this.extentId}-casing`, `${this.extentId}-line`];
    if (this.extent && !this.dropped.has(this.extentId)) {
      reg.call(this.app, {
        id: this.extentId, name: `Extent ${this.label}`, type: "geojson", nativeLayerIds: ext, sourceIds: [this.extentId],
        geojson: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [this.extent] } }] },
        metadata: this.bounds ? { bounds: this.bounds } : {}, groupId: this.groupId, paintMode: "plugin",
        paintBridge: {
          setOpacity: (o: number) => ext.forEach((l) => this.map?.setPaintProperty(l, "line-opacity", o)),
          setVisibility: (v: boolean) => {
            this.visible.extent = v;
            ext.forEach((l) => this.map?.setLayoutProperty(l, "visibility", v ? "visible" : "none"));
          },
        },
      } as any);
    }
    this.registered = true;
  }

  private removeNative(base: string): void {
    const map = this.map;
    for (const id of this.ids(base)) {
      if (map?.getLayer(id)) map.removeLayer(id);
      if (map?.getSource(id)) map.removeSource(id);
    }
  }

  /** Remove every layer and source this run added. */
  clear(): void {
    this.map?.off?.("style.load", this.onStyle);
    for (const base of [this.changeId, this.coverageId]) {
      this.removeNative(base);
      this.app.unregisterExternalNativeLayer?.(base);
    }
    this.removeExtent();
    this.app.unregisterExternalNativeLayer?.(this.extentId);
    this.tiles = [];
  }

  get count(): number {
    return this.tiles.length;
  }
}

function overlaps(a: Window, b: Window): boolean {
  return a[0] < b[1] && b[0] < a[1] && a[2] < b[3] && b[2] < a[3];
}
