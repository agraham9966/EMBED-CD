// Click an object on the map to label it (or, in Inspect, to read it). Not a GeoLibre map tool:
// it listens to MapLibre clicks while on, and stands aside whenever one of GeoLibre's own tools
// (Identify, selection, the editor) owns the pointer.
import type { GeoLibreAppAPI } from "../host-api";

const TOL = 3;                  // px around the click, as the QGIS tool's three-pixel box

export class ClickTool {
  private active = false;

  constructor(
    private app: GeoLibreAppAPI,
    private fillLayerId: () => string | null,
    private onClick: (row: number | null, right: boolean) => void,
  ) {}

  private get map(): any {
    return (this.app as any).getMap?.();
  }

  private handle = (right: boolean) => (e: any) => {
    if (((this.app as any).getActiveMapTool?.() ?? null) !== null) return;
    const layer = this.fillLayerId(), map = this.map;
    if (!layer || !map) return;
    if (right) e.preventDefault?.();
    const { x, y } = e.point;
    const hit = map.queryRenderedFeatures([[x - TOL, y - TOL], [x + TOL, y + TOL]], { layers: [layer] })[0];
    const id = Number(hit?.properties?.id);
    this.onClick(Number.isFinite(id) && id > 0 ? id - 1 : null, right);
  };
  private left = this.handle(false);
  private right = this.handle(true);

  on(): void {
    if (this.active || !this.map) return;
    this.active = true;
    this.map.on("click", this.left);
    this.map.on("contextmenu", this.right);
    this.map.getCanvas().style.cursor = "crosshair";
  }

  off(): void {
    if (!this.active) return;
    this.active = false;
    this.map?.off("click", this.left);
    this.map?.off("contextmenu", this.right);
    if (this.map) this.map.getCanvas().style.cursor = "";
  }

  get isOn(): boolean {
    return this.active;
  }
}
