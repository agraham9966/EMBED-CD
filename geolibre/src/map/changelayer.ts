// The change map and its data-coverage layer on the MapLibre map. Each tile is a canvas placed by
// its four corners; overlapping tiles are drawn through the mosaic rules (engine/job compose), so
// where two tiles overlap they show the same pixels. Colours copy the QGIS plugin (dock.py).
import { transformer } from "../engine/crs";
import type { Grid, Window } from "../engine/grid";
import { compose, type TileResult } from "../engine/job";
import { NODATA } from "../engine/score";
import type { GeoLibreAppAPI } from "../host-api";

export const CHANGE_OPACITY = 0.85;
type RGBA = [number, number, number, number];
const RAMP: [number, [number, number, number]][] = [[0, [0, 210, 235]], [0.4, [255, 225, 40]], [0.8, [235, 30, 30]]];
const COVERAGE: RGBA[] = [[45, 50, 60, 60], [0, 0, 0, 0], [214, 60, 200, 220], [60, 170, 235, 220], [255, 255, 255, 230]];

/** Cyan -> yellow -> red over 0..0.8; transparent at or below the cutoff and for no data. */
export function scoreColour(v: number, cutoff: number): RGBA {
  if (v === NODATA || !(v > cutoff)) return [0, 0, 0, 0];
  if (v >= RAMP[2][0]) return [...RAMP[2][1], 255];
  const i = v < RAMP[1][0] ? 0 : 1;
  const [a, ca] = RAMP[i], [b, cb] = RAMP[i + 1];
  const t = (Math.max(v, 0) - a) / (b - a);
  return [0, 1, 2].map((k) => Math.round(ca[k] + (cb[k] - ca[k]) * t)).concat(255) as RGBA;
}

export const coverageColour = (code: number): RGBA => COVERAGE[code] ?? [0, 0, 0, 0];

/** The window's corners in lon/lat, in MapLibre's order: top-left, top-right, bottom-right, bottom-left. */
export function tileCorners(g: Grid, [r0, r1, c0, c1]: Window): [number, number][] {
  const f = transformer(g.crs, "EPSG:4326");
  const x = (c: number) => g.x0 + c * g.res, y = (r: number) => g.y0 - r * g.res;
  return [f(x(c0), y(r0)), f(x(c1), y(r0)), f(x(c1), y(r1)), f(x(c0), y(r1))];
}

type Drawn = {
  result: TileResult; score: Float32Array; cov: Uint8Array;
  canvas: HTMLCanvasElement; covCanvas: HTMLCanvasElement; coordinates: [number, number][];
};

/** The style has its JSON (sources and layers can be added). */
export const styleReady = (map: any): boolean => !!map?.style?._loaded;

export class ChangeLayer {
  private tiles: Drawn[] = [];
  private cutoff = 0.15;
  private pending = false;
  private visible = { change: true, coverage: true };
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
    const [r0, r1, c0, c1] = result.window;
    const canvas = document.createElement("canvas"), covCanvas = document.createElement("canvas");
    canvas.width = covCanvas.width = c1 - c0;
    canvas.height = covCanvas.height = r1 - r0;
    const all = [...this.tiles.map((t) => t.result), result];
    const d: Drawn = { result, ...compose(result.window, all), canvas, covCanvas, coordinates: tileCorners(grid, result.window) };
    this.tiles.push(d);
    for (const t of this.tiles.slice(0, -1)) {
      if (overlaps(t.result.window, result.window)) {
        Object.assign(t, compose(t.result.window, all));
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

  private recolourAll(): void {
    this.tiles.forEach((t, i) => {
      this.paintScore(t);
      this.refresh(i);
    });
  }

  private paint(t: Drawn): void {
    this.paintScore(t);
    const ctx = t.covCanvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(t.covCanvas.width, t.covCanvas.height);
    for (let p = 0; p < t.cov.length; p++) img.data.set(coverageColour(t.cov[p]), p * 4);
    ctx.putImageData(img, 0, 0);
  }

  private paintScore(t: Drawn): void {
    const ctx = t.canvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(t.canvas.width, t.canvas.height);
    for (let p = 0; p < t.score.length; p++) img.data.set(scoreColour(t.score[p], this.cutoff), p * 4);
    ctx.putImageData(img, 0, 0);
  }

  /** A canvas source re-uploads only while playing: play for one frame, then pause. */
  private refresh(i: number): void {
    const map = this.map;
    for (const id of [`${this.changeId}-${i}`, `${this.coverageId}-${i}`]) {
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
