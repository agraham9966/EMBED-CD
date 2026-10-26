// The before/after lens (QGIS lens.py): hover the map to see the before year's imagery in a circle
// under the cursor, hold A for the after year, Shift+wheel to resize.
//
// It brings its own imagery: a second, non-interactive MapLibre map (GeoLibre's own MapLibre, so
// nothing extra is bundled) laid exactly over the main map, kept on the same camera, and cut to a
// circle at the cursor with CSS. Moving the mouse only moves the circle, so it costs nothing; no
// basemap needs to be open and the Layers panel is untouched. The overlay never takes the pointer,
// so clicks still reach the map: labelling keeps working with the lens on.
import type { FeatureCollection } from "geojson";
import type { GeoLibreAppAPI } from "../host-api";
import { nearestYear, tileUrl, ZMAX } from "./eox";

export const MIN_R = 40, MAX_R = 400;
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

export const editable = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

export class CompareLens {
  radius = 100;
  years: [number, number] | null = null;      // EOX years actually shown: before, after
  after = false;                              // A held
  /** Shift+wheel changed the size (the settings box follows). */
  onRadiusChanged?: (r: number) => void;
  /** Why the lens could not start, if it could not. */
  error = "";

  private lens: any = null;
  private wrap: HTMLDivElement | null = null;
  private clip: HTMLDivElement | null = null;
  private ring: HTMLDivElement | null = null;
  private tag: HTMLDivElement | null = null;
  private pos: { x: number; y: number } | null = null;
  private outlines: FeatureCollection = EMPTY;
  private projType: string | undefined;

  constructor(private app: GeoLibreAppAPI) {}

  private get map(): any {
    return (this.app as any).getMap?.();
  }

  get active(): boolean {
    return !!this.lens;
  }

  setActive(on: boolean): void {
    if (on === this.active) return;
    on ? this.start() : this.stop();
  }

  setYears(before: number, after: number): void {
    const y: [number, number] = [nearestYear(before), nearestYear(after)];
    if (this.years && y[0] === this.years[0] && y[1] === this.years[1]) return;
    this.years = y;
    if (this.lens) { this.stop(); this.start(); }      // new sources: simplest to rebuild
  }

  setRadius(r: number): void {
    this.radius = Math.max(MIN_R, Math.min(MAX_R, Math.round(r)));
    this.place();
  }

  /** The objects' outlines (lon/lat), drawn in the lens so you can see where an edge falls. */
  setOutlines(fc: FeatureCollection | null): void {
    this.outlines = fc ?? EMPTY;
    this.lens?.getSource?.("outlines")?.setData(this.outlines);
  }

  shownYear(): number | null {
    return this.years ? this.years[this.after ? 1 : 0] : null;
  }

  private start(): void {
    const main = this.map;
    if (!main || !this.years) return;
    const container: HTMLElement = main.getContainer();
    const wrap = document.createElement("div");
    wrap.className = "ecd-lens";
    const clip = document.createElement("div");
    clip.className = "ecd-lens-clip";
    const inner = document.createElement("div");
    inner.className = "ecd-lens-map";
    const ring = document.createElement("div");
    ring.className = "ecd-lens-ring";
    const tag = document.createElement("div");
    tag.className = "ecd-lens-tag";
    clip.append(inner);
    wrap.append(clip, ring, tag);
    // Under the map's own controls, over its canvas.
    container.insertBefore(wrap, container.querySelector(".maplibregl-control-container"));
    const [before, after] = this.years;
    const raster = (y: number) => ({ type: "raster", tiles: [tileUrl(y)], tileSize: 256, maxzoom: ZMAX });
    try {
      const Map = main.constructor;
      this.lens = new Map({
        container: inner, interactive: false, attributionControl: false, fadeDuration: 0,
        center: main.getCenter(), zoom: main.getZoom(), bearing: main.getBearing(), pitch: main.getPitch(),
        style: {
          version: 8,
          sources: { before: raster(before), after: raster(after), outlines: { type: "geojson", data: this.outlines } },
          layers: [
            { id: "loading", type: "background", paint: { "background-color": "rgba(40,40,40,0.63)" } },
            // Both years stay loaded so pressing A is instant: the after year sits underneath and
            // the before year's opacity drops to 0 while A is held.
            { id: "after", type: "raster", source: "after", paint: { "raster-fade-duration": 0 } },
            { id: "before", type: "raster", source: "before",
              paint: { "raster-fade-duration": 0, "raster-opacity-transition": { duration: 0, delay: 0 } } },
            { id: "outlines", type: "line", source: "outlines", paint: { "line-color": "rgba(255,255,255,0.9)", "line-width": 1.4 } },
          ],
        },
      });
    } catch (e) {
      this.error = `The lens could not start: ${(e as Error)?.message ?? e}`;
      wrap.remove();
      return;
    }
    this.error = "";
    Object.assign(this, { wrap, clip, ring, tag });
    this.projType = undefined;
    this.sync();
    main.on("move", this.sync);
    main.on("resize", this.onResize);
    main.on("mousemove", this.onMove);
    container.addEventListener("mouseleave", this.onLeave);
    container.addEventListener("wheel", this.onWheel, { capture: true, passive: false });
    window.addEventListener("keydown", this.onKey, true);
    window.addEventListener("keyup", this.onKey, true);
    window.addEventListener("blur", this.onBlur);
    this.place();
  }

  private stop(): void {
    const main = this.map;
    main?.off?.("move", this.sync);
    main?.off?.("resize", this.onResize);
    main?.off?.("mousemove", this.onMove);
    const container: HTMLElement | undefined = main?.getContainer?.();
    container?.removeEventListener("mouseleave", this.onLeave);
    container?.removeEventListener("wheel", this.onWheel, { capture: true } as any);
    window.removeEventListener("keydown", this.onKey, true);
    window.removeEventListener("keyup", this.onKey, true);
    window.removeEventListener("blur", this.onBlur);
    try { this.lens?.remove(); } catch { /* already gone with the page */ }
    this.wrap?.remove();
    this.lens = this.wrap = this.clip = this.ring = this.tag = null;
    this.after = false;
  }

  /** Same camera as the main map, every frame it moves. */
  private sync = () => {
    const main = this.map, lens = this.lens;
    if (!main || !lens) return;
    const proj = main.getProjection?.();
    if (proj?.type !== this.projType) {
      this.projType = proj?.type;
      if (proj) {
        try { lens.setProjection(proj); } catch { /* style still loading: retried on the next move */ this.projType = undefined; }
      }
    }
    lens.jumpTo({ center: main.getCenter(), zoom: main.getZoom(), bearing: main.getBearing(), pitch: main.getPitch(),
      padding: main.getPadding?.() });
  };

  private onResize = () => { this.lens?.resize(); this.sync(); };
  private onMove = (e: any) => { this.pos = { x: e.point.x, y: e.point.y }; this.place(); };
  private onLeave = () => { this.pos = null; this.place(); };
  private onBlur = () => this.flip(false);

  private onKey = (e: KeyboardEvent) => {
    if (e.key !== "a" && e.key !== "A") return;
    if (!this.pos || editable(e.target)) return;     // typing a name, or not over the map
    e.preventDefault();
    e.stopPropagation();                             // claim A before a GeoLibre shortcut does
    if (!e.repeat) this.flip(e.type === "keydown");
  };

  private onWheel = (e: WheelEvent) => {
    if (!e.shiftKey) return;
    e.preventDefault();
    e.stopPropagation();
    const step = e.deltaY || e.deltaX;              // Shift can turn the wheel horizontal
    this.setRadius(this.radius + (step < 0 ? 10 : -10));
    this.onRadiusChanged?.(this.radius);
  };

  private flip(after: boolean): void {
    if (after === this.after) return;
    this.after = after;
    if (this.lens?.getLayer?.("before")) this.lens.setPaintProperty("before", "raster-opacity", after ? 0 : 1);
    this.place();
  }

  /** Put the circle, ring and year tag at the cursor (or hide them off the map). */
  private place(): void {
    const { clip, ring, tag, pos, radius: r } = this;
    if (!clip || !ring || !tag) return;
    const show = !!pos;
    clip.style.clipPath = show ? `circle(${r}px at ${pos!.x}px ${pos!.y}px)` : "circle(0px at 0px 0px)";
    ring.hidden = tag.hidden = !show;
    if (!show) return;
    Object.assign(ring.style, { left: `${pos!.x - r}px`, top: `${pos!.y - r}px`, width: `${2 * r}px`, height: `${2 * r}px` });
    ring.classList.toggle("ecd-lens-after", this.after);
    tag.textContent = `${this.shownYear()} · ${this.after ? "after" : "before (hold A: after)"}`;
    Object.assign(tag.style, { left: `${pos!.x}px`, top: `${pos!.y + r + 4}px` });
  }

  dispose(): void {
    this.stop();
  }
}
