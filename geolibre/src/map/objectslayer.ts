// The objects on the map: plugin-owned MapLibre layers, registered with GeoLibre together with
// their GeoJSON (so Zoom to layer and Identify work). Styled as the QGIS plugin's _style: each
// class in its colour, "unknown" white, "not classified yet" faint; an outline is DASHED where you
// labelled the object and solid where the model predicted it; the current object is outlined yellow.
import type { FeatureCollection } from "geojson";
import { transformer } from "../engine/crs";
import type { Grid } from "../engine/grid";
import type { ObjectPoly } from "../engine/objects";
import type { GeoLibreAppAPI } from "../host-api";
import { styleReady } from "./changelayer";

export function objectsGeoJSON(polys: ObjectPoly[], grid: Grid): FeatureCollection {
  const f = transformer(grid.crs, "EPSG:4326");
  return {
    type: "FeatureCollection",
    features: polys.map((p, i) => ({
      type: "Feature",
      id: i + 1,
      properties: { id: i + 1, area_ha: p.areaHa, chg_mean: p.chgMean, chg_max: p.chgMax, n_px: p.nPx, predicted: "" },
      geometry: { type: "Polygon", coordinates: p.rings.map((r) => r.map(([x, y]) => f(x, y))) },
    })),
  };
}

const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

/** MapLibre expressions for the fill and outline, keyed on the `predicted` property. */
export function objectStyle(colors: Record<string, string>) {
  const fill: unknown[] = ["match", ["get", "predicted"]];
  const lineColor: unknown[] = ["match", ["get", "predicted"]];
  const lineWidth: unknown[] = ["match", ["get", "predicted"]];
  for (const [name, hex] of Object.entries(colors)) {
    const [r, g, b] = rgb(hex);
    fill.push(name, `rgba(${r},${g},${b},0.431)`);         // alpha 110/255
    lineColor.push(name, `rgb(${r},${g},${b})`);
    lineWidth.push(name, 3);                               // 0.8 mm
  }
  fill.push("unknown", "rgba(255,255,255,0.118)", "rgba(255,255,255,0.086)");
  lineColor.push("unknown", "rgb(255,255,255)", "rgba(235,235,235,0.863)");
  lineWidth.push("unknown", 3.8, 2.6);                     // 1.0 mm; not classified 0.7 mm
  return { fill, lineColor, lineWidth };
}

export class ObjectsLayer {
  readonly id: string;
  private fc: FeatureCollection | null = null;
  private visible = true;
  private colors: Record<string, string> = {};
  private current: number | null = null;
  /** The run's Layers-panel group. */
  groupId?: string;

  constructor(private app: GeoLibreAppAPI, runId: string, private label: string) {
    this.id = `ecd-${runId}-objects`;
    this.map?.on?.("style.load", this.onStyle);     // a basemap switch drops our layers
  }

  private get map(): any {
    return (this.app as any).getMap?.();
  }

  private onStyle = () => this.addToMap();
  private get layerIds() {
    return [`${this.id}-fill`, `${this.id}-casing`, `${this.id}-line`, `${this.id}-labelled`, `${this.id}-current`];
  }

  /** Object `row` as GeoJSON (lon/lat), or null. */
  feature(row: number): FeatureCollection["features"][number] | null {
    return this.fc?.features[row] ?? null;
  }

  /** All the objects as GeoJSON (lon/lat), or null before any are set. */
  get data(): FeatureCollection | null {
    return this.fc;
  }

  /** The id MapLibre answers a click with, for the fill layer. */
  get fillLayerId(): string {
    return `${this.id}-fill`;
  }

  set(polys: ObjectPoly[], grid: Grid): void {
    this.fc = objectsGeoJSON(polys, grid);
    this.push();
    this.register();
  }

  /** Write each object's label, prediction and confidence, and restyle for these classes. */
  update(pred: string[] | null, labels: Map<number, string>, colors: Record<string, string>, conf?: Float64Array | null): void {
    if (!this.fc) return;
    this.colors = { ...colors };
    this.fc.features.forEach((f, i) => {
      Object.assign(f.properties!, { predicted: pred?.[i] ?? "", label: labels.get(i) ?? "", confidence: conf ? conf[i] : 0 });
    });
    this.push();
    this.restyle();
    this.register();
  }

  highlight(row: number | null): void {
    this.current = row;
    if (this.map?.getLayer(`${this.id}-current`)) this.map.setFilter(`${this.id}-current`, ["==", ["get", "id"], row === null ? -1 : row + 1]);
  }

  private push(): void {
    const src = this.map?.getSource(this.id);
    if (src) src.setData(this.fc);
    else this.addToMap();
  }

  private restyle(): void {
    const map = this.map, s = objectStyle(this.colors);
    if (!map?.getLayer(`${this.id}-fill`)) return;
    map.setPaintProperty(`${this.id}-fill`, "fill-color", s.fill);
    for (const l of [`${this.id}-line`, `${this.id}-labelled`]) {
      map.setPaintProperty(l, "line-color", s.lineColor);
      map.setPaintProperty(l, "line-width", s.lineWidth);
    }
    map.setPaintProperty(`${this.id}-casing`, "line-width", ["+", s.lineWidth, 2]);
  }

  private addToMap(): void {
    const map = this.map;
    if (!map || !this.fc || !styleReady(map) || map.getSource(this.id)) return;
    const layout = { visibility: this.visible ? "visible" : "none" };
    const s = objectStyle(this.colors);
    map.addSource(this.id, { type: "geojson", data: this.fc });
    map.addLayer({ id: `${this.id}-fill`, type: "fill", source: this.id, layout, paint: { "fill-color": s.fill } });
    // A thin dark edge under every outline, so the white "not classified" lines still show on a
    // light basemap (and class colours keep an edge on bright imagery).
    map.addLayer({ id: `${this.id}-casing`, type: "line", source: this.id, layout,
      paint: { "line-color": "rgba(20,22,28,0.55)", "line-width": ["+", s.lineWidth, 2] } });
    map.addLayer({ id: `${this.id}-line`, type: "line", source: this.id, layout, filter: ["==", ["get", "label"], ""],
      paint: { "line-color": s.lineColor, "line-width": s.lineWidth } });
    map.addLayer({ id: `${this.id}-labelled`, type: "line", source: this.id, layout, filter: ["!=", ["get", "label"], ""],
      paint: { "line-color": s.lineColor, "line-width": s.lineWidth, "line-dasharray": [2, 1.5] } });
    map.addLayer({ id: `${this.id}-current`, type: "line", source: this.id, layout,
      filter: ["==", ["get", "id"], this.current === null ? -1 : this.current + 1],
      paint: { "line-color": "rgb(255,215,0)", "line-width": 3 } });
  }

  private register(): void {
    const ids = this.layerIds;
    this.app.registerExternalNativeLayer?.({
      id: this.id, name: `Objects ${this.label}`, type: "geojson", nativeLayerIds: ids, sourceIds: [this.id],
      geojson: this.fc ?? undefined, paintMode: "plugin", groupId: this.groupId,
      paintBridge: {
        setOpacity: (o: number) => {
          this.map?.setPaintProperty(`${this.id}-fill`, "fill-opacity", o);
          for (const l of ids.slice(1)) this.map?.setPaintProperty(l, "line-opacity", o);
        },
        setVisibility: (v: boolean) => {
          this.visible = v;
          ids.forEach((l) => this.map?.setLayoutProperty(l, "visibility", v ? "visible" : "none"));
        },
      },
    } as any);
  }

  clear(): void {
    const map = this.map;
    map?.off?.("style.load", this.onStyle);
    for (const l of [...this.layerIds].reverse()) if (map?.getLayer(l)) map.removeLayer(l);
    if (map?.getSource(this.id)) map.removeSource(this.id);
    this.app.unregisterExternalNativeLayer?.(this.id);
    this.fc = null;
  }
}
