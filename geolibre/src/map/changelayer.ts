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
const TOP = 0.8;                                  // the ramp's top colour from this score up
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

/** Turbo over 0..0.8, from a tenth of the way in: the fit's first tenth is near-black, and Auto
 *  cutoffs are often that low. Transparent at or below the cutoff and for no data. */
export function scoreColour(v: number, cutoff: number): RGBA {
  if (v === NODATA || !(v > cutoff)) return [0, 0, 0, 0];
  return [...turbo(0.1 + 0.9 * Math.min(Math.max(v, 0) / TOP, 1)), 255];
}

export const coverageColour = (code: number): RGBA => COVERAGE[code] ?? [0, 0, 0, 0];

/** RGBA as one little-endian 32-bit pixel, for writing an ImageData through a Uint32Array. */
const pack = ([r, g, b, a]: RGBA) => (r | (g << 8) | (b << 16) | (a << 24)) >>> 0;
// The ramp in steps of 0.001 (indistinguishable on screen); everything past 0.8 is the top colour.
const LUT_STEPS = 1000 * TOP;
const SCORE_LUT = Uint32Array.from({ length: LUT_STEPS + 1 }, (_, i) => pack(scoreColour(i / 1000, -Infinity)));
const COVERAGE_PX = COVERAGE.map(pack);

/** Each pixel's colour ignoring the cutoff (nodata transparent): worked out once per tile. */
export function scoreColours(score: Float32Array): Uint32Array {
  const out = new Uint32Array(score.length);
  for (let p = 0; p < score.length; p++) {
    const v = score[p];
    if (v !== NODATA && v >= 0) out[p] = SCORE_LUT[Math.min(LUT_STEPS, Math.round(v * 1000))];
  }
  return out;
}

/** The window's corners in lon/lat, in MapLibre's order: top-left, top-right, bottom-right, bottom-left. */
export function tileCorners(g: Grid, [r0, r1, c0, c1]: Window): [number, number][] {
  const f = transformer(g.crs, "EPSG:4326");
  const x = (c: number) => g.x0 + c * g.res, y = (r: number) => g.y0 - r * g.res;
  return [f(x(c0), y(r0)), f(x(c1), y(r0)), f(x(c1), y(r1)), f(x(c0), y(r1))];
}

type Drawn = {
  result: TileResult; score: Float32Array; cov: Uint8Array;
  /** What the canvas covers: the tile's window plus one pixel right and down (see addTile). */
  win: Window;
  canvas: HTMLCanvasElement; covCanvas: HTMLCanvasElement; coordinates: [number, number][];
  /** Cutoff-free colour per pixel, and the image the cutoff is applied into (kept, not remade). */
  colours?: Uint32Array; img?: ImageData;
};

/** The style has its JSON (sources and layers can be added). */
export const styleReady = (map: any): boolean => !!map?.style?._loaded;

export class ChangeLayer {
  private tiles: Drawn[] = [];
  private cutoff = 0.15;
  private pending = false;
  private visible = { change: true, coverage: true };
  private bounds: Bbox | null = null;            // lon/lat, for GeoLibre's Zoom to layer
  /** The run's Layers-panel group, set before the first tile is registered. */
  groupId?: string;
  readonly changeId: string;
  readonly coverageId: string;

  constructor(
    private app: GeoLibreAppAPI,
    private runId: string,
    private label: string,                       // "2019-2024"
    private schedule: (f: () => void) => void = (f) => requestAnimationFrame(f),
  ) {
    this.changeId = `ecd-${runId}-change`;
    this.coverageId = `ecd-${runId}-coverage`;
    // GeoLibre swaps the whole style on a basemap change (setStyle, diff: false), which drops
    // every source and layer a plugin added. Put ours back each time.
    this.map?.on?.("style.load", this.onStyle);
  }

  private onStyle = () => this.tiles.forEach((_, i) => this.addToMap(i));

  private get map(): any {
    return (this.app as any).getMap?.();
  }

  /** A finished tile: draw it, and redraw any tile it overlaps so overlaps agree. */
  addTile(grid: Grid, result: TileResult): void {
    const map = this.map;
    if (!map) return;
    const i = this.tiles.length;
    this.bounds ??= transformBounds(grid.crs, "EPSG:4326",
      [grid.x0, grid.y0 - grid.height * grid.res, grid.x0 + grid.width * grid.res, grid.y0]);
    // One pixel past the tile's own right and bottom edge, filled from the neighbour through the
    // same mosaic rules: two image quads that merely touch can leave a hairline of map showing
    // between them (it showed through the coverage layer as tile outlines).
    const [r0, r1, c0, c1] = result.window;
    const win: Window = [r0, Math.min(r1 + 1, grid.height), c0, Math.min(c1 + 1, grid.width)];
    const canvas = document.createElement("canvas"), covCanvas = document.createElement("canvas");
    canvas.width = covCanvas.width = win[3] - win[2];
    canvas.height = covCanvas.height = win[1] - win[0];
    const all = [...this.tiles.map((t) => t.result), result];
    const d: Drawn = { result, win, ...compose(win, all), canvas, covCanvas, coordinates: tileCorners(grid, win) };
    this.tiles.push(d);
    for (const t of this.tiles.slice(0, -1)) {
      if (overlaps(t.win, result.window)) {
        Object.assign(t, compose(t.win, all));
        this.paint(t);
        this.refresh(this.tiles.indexOf(t));
      }
    }
    this.paint(d);
    this.addToMap(i);
    this.register();
  }

  /** Add tile i's sources and layers, unless the style is mid-swap (style.load re-adds them). */
  private addToMap(i: number): void {
    const map = this.map, t = this.tiles[i];
    if (!map || !t || !styleReady(map) || map.getSource(`${this.changeId}-${i}`)) return;
    const { coordinates } = t;
    map.addSource(`${this.coverageId}-${i}`, { type: "canvas", canvas: t.covCanvas, coordinates, animate: false });
    map.addSource(`${this.changeId}-${i}`, { type: "canvas", canvas: t.canvas, coordinates, animate: false });
    // Coverage goes under every change tile; change tiles stack on top.
    map.addLayer({ id: `${this.coverageId}-${i}`, type: "raster", source: `${this.coverageId}-${i}`,
      layout: { visibility: this.visible.coverage ? "visible" : "none" },
      paint: { "raster-fade-duration": 0, "raster-resampling": "nearest" } },
      map.getLayer(`${this.changeId}-0`) ? `${this.changeId}-0` : undefined);
    map.addLayer({ id: `${this.changeId}-${i}`, type: "raster", source: `${this.changeId}-${i}`,
      layout: { visibility: this.visible.change ? "visible" : "none" },
      paint: { "raster-opacity": CHANGE_OPACITY, "raster-fade-duration": 0, "raster-resampling": "nearest" } });
  }

  /** Coalesced to one recolour per frame: dragging the slider never queues work up. */
  setCutoff(t: number): void {
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

  /** New or recomposed pixels: work out their colours, then draw both canvases. */
  private paint(t: Drawn): void {
    t.colours = scoreColours(t.score);
    t.img = undefined;
    this.paintScore(t);
    const ctx = t.covCanvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(t.covCanvas.width, t.covCanvas.height);
    const px = new Uint32Array(img.data.buffer);
    for (let p = 0; p < t.cov.length; p++) px[p] = COVERAGE_PX[t.cov[p]] ?? 0;
    ctx.putImageData(img, 0, 0);
  }

  /** The slider's whole cost: one comparison per pixel, into an image kept from last time. */
  private paintScore(t: Drawn): void {
    const ctx = t.canvas.getContext("2d");
    if (!ctx || !t.colours) return;
    t.img ??= ctx.createImageData(t.canvas.width, t.canvas.height);
    const px = new Uint32Array(t.img.data.buffer), s = t.score, col = t.colours, c = this.cutoff;
    for (let p = 0; p < s.length; p++) px[p] = s[p] > c ? col[p] : 0;
    ctx.putImageData(t.img, 0, 0);
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

  private register(): void {
    const reg = this.app.registerExternalNativeLayer;
    if (!reg) return;
    for (const [id, name, key, opacity] of [
      [this.changeId, `Change ${this.label}`, "change", CHANGE_OPACITY],
      [this.coverageId, `Data coverage ${this.label}`, "coverage", 1],
    ] as const) {
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
  }

  /** Remove every layer and source this run added. */
  clear(): void {
    const map = this.map;
    map?.off?.("style.load", this.onStyle);
    for (const base of [this.changeId, this.coverageId]) {
      for (const id of this.ids(base)) {
        if (map?.getLayer(id)) map.removeLayer(id);
        if (map?.getSource(id)) map.removeSource(id);
      }
      this.app.unregisterExternalNativeLayer?.(base);
    }
    this.tiles = [];
  }

  get count(): number {
    return this.tiles.length;
  }
}

function overlaps(a: Window, b: Window): boolean {
  return a[0] < b[1] && b[0] < a[1] && a[2] < b[3] && b[2] < a[3];
}
