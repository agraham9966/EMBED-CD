// The objects on the map: plugin-owned MapLibre fill and line layers, registered with GeoLibre
// together with their GeoJSON (so Zoom to layer and Identify work). Before any class exists every
// object is "not classified yet": a faint fill and a dashed outline, as in the QGIS plugin.
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

export class ObjectsLayer {
  readonly id: string;
  private fc: FeatureCollection | null = null;
  private visible = true;

  constructor(private app: GeoLibreAppAPI, runId: string, private label: string) {
    this.id = `ecd-${runId}-objects`;
    this.map?.on?.("style.load", this.onStyle);     // a basemap switch drops our layers
  }

  private get map(): any {
    return (this.app as any).getMap?.();
  }

  private onStyle = () => this.addToMap();

  set(polys: ObjectPoly[], grid: Grid): void {
    this.fc = objectsGeoJSON(polys, grid);
    const src = this.map?.getSource(this.id);
    if (src) src.setData(this.fc);
    else this.addToMap();
    this.register();
  }

  private addToMap(): void {
    const map = this.map;
    if (!map || !this.fc || !styleReady(map) || map.getSource(this.id)) return;
    const vis = this.visible ? "visible" : "none";
    map.addSource(this.id, { type: "geojson", data: this.fc });
    map.addLayer({ id: `${this.id}-fill`, type: "fill", source: this.id, layout: { visibility: vis },
      paint: { "fill-color": "rgba(255,255,255,0.086)" } });
    map.addLayer({ id: `${this.id}-line`, type: "line", source: this.id, layout: { visibility: vis },
      paint: { "line-color": "rgba(235,235,235,0.86)", "line-width": 2, "line-dasharray": [2, 1.5] } });
  }

  private register(): void {
    const ids = [`${this.id}-fill`, `${this.id}-line`];
    this.app.registerExternalNativeLayer?.({
      id: this.id, name: `Objects ${this.label}`, type: "geojson", nativeLayerIds: ids, sourceIds: [this.id],
      geojson: this.fc ?? undefined, paintMode: "plugin",
      paintBridge: {
        setOpacity: (o: number) => {
          this.map?.setPaintProperty(`${this.id}-fill`, "fill-opacity", o);
          this.map?.setPaintProperty(`${this.id}-line`, "line-opacity", o);
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
    for (const l of [`${this.id}-line`, `${this.id}-fill`]) if (map?.getLayer(l)) map.removeLayer(l);
    if (map?.getSource(this.id)) map.removeSource(this.id);
    this.app.unregisterExternalNativeLayer?.(this.id);
    this.fc = null;
  }
}
