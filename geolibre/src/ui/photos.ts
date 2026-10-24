// Reference imagery (QGIS dock "Photo:" strip): one toggle per AlphaEarth year, each adding that
// year's Sentinel-2 cloudless imagery as an ordinary GeoLibre layer at the bottom of the Layers
// panel. Independent, not exclusive: comparing years means having several loaded and flicking
// their eyes. Outside the steps, because it is useful before any area is drawn.
import type { GeoLibreAppAPI } from "../host-api";
import { ATTRIBUTION, LICENCE, layerName, nearestYear, tileUrl, ZMAX } from "../map/eox";
import { el } from "./dom";
import { YEARS } from "./step1";

/** Year -> GeoLibre layer id. Module-level: the layers outlive the panel closing. */
const photoIds = new Map<number, string>();

const layers = (app: GeoLibreAppAPI) => ((app as any).listLayers?.() ?? []) as { id: string; type: string }[];

/** Forget years whose layer the user removed in the Layers panel. */
function prune(app: GeoLibreAppAPI): void {
  const have = new Set(layers(app).map((l) => l.id));
  for (const [y, id] of photoIds) if (!have.has(id)) photoIds.delete(y);
}

/** Strips on screen re-read the loaded years through this (one panel at a time). */
let resync: (() => void) | null = null;

/** Make sure `year`'s imagery is on the map (Make change map puts the after year under the
 *  result). Nothing is added when the same EOX year is already loaded, from the strip or an
 *  earlier run: 2017 and 2016 are one mosaic. */
export function showPhoto(app: GeoLibreAppAPI, year: number): void {
  prune(app);
  const got = nearestYear(year);
  if ([...photoIds.keys()].some((y) => nearestYear(y) === got)) return;
  addPhoto(app, year);
  resync?.();
}

function addPhoto(app: GeoLibreAppAPI, y: number): void {
  if (typeof (app as any).addTileLayer !== "function") return;
  const got = nearestYear(y);
  // Bottom of the panel, just above the basemap: a backdrop to check the change map against,
  // never a cover over it.
  const bottom = layers(app).find((l) => l.type !== "background")?.id;
  photoIds.set(y, (app as any).addTileLayer(layerName(got), tileUrl(got), {
    attribution: ATTRIBUTION, maxzoom: ZMAX, ...(bottom ? { beforeLayerId: bottom } : {}),
  }));
}

/** Remove every photo layer (the plugin is being switched off). */
export function clearPhotos(app: GeoLibreAppAPI): void {
  for (const id of photoIds.values()) (app as any).removeLayer?.(id);
  photoIds.clear();
}

export function buildPhotos(app: GeoLibreAppAPI): HTMLElement {
  const root = el("div", "ecd-photos");
  const note = el("p", "ecd-muted", "");
  const setStatus = (s: string) => { note.textContent = s; };
  const canAdd = typeof (app as any).addTileLayer === "function";
  const strip = el("div", "ecd-row ecd-photo-row");
  const label = el("span", "", "Photo:");
  label.title = "Sentinel-2 cloudless imagery (EOX), one year per button, so you can check what the change map "
    + "is claiming and see which year a change appeared in.\n\nEach year is its own layer at the bottom of the "
    + "Layers panel; turn on several and flick their eyes to compare. Click a year again to remove it.";
  strip.append(label);
  const btns = new Map<number, HTMLButtonElement>();
  for (const y of YEARS) {
    const got = nearestYear(y);
    const b = el("button", "ecd-btn ecd-small ecd-photo", String(y % 100).padStart(2, "0")) as HTMLButtonElement;
    // A year EOX does not publish says so, rather than showing a neighbour under the wrong label.
    b.title = got === y ? `Sentinel-2 cloudless ${y}.` : `No ${y} mosaic exists: this button shows ${got} instead.`;
    b.disabled = !canAdd;
    b.onclick = () => toggle(y);
    btns.set(y, b);
    strip.append(b);
  }
  const credit = el("p", "ecd-credit", `${ATTRIBUTION} · ${LICENCE}`);
  root.append(el("hr", "ecd-rule"), el("p", "ecd-head", "Reference imagery"), strip, note, credit);

  function sync(): void {
    prune(app);
    for (const [y, b] of btns) b.classList.toggle("ecd-on", photoIds.has(y));
  }

  function toggle(y: number): void {
    prune(app);
    const id = photoIds.get(y);
    if (id) {
      (app as any).removeLayer?.(id);
      photoIds.delete(y);
    } else {
      const got = nearestYear(y);
      try {
        addPhoto(app, y);
        setStatus(got === y ? "" : `No ${y} mosaic: showing ${got}.`);
      } catch (e) {
        setStatus(`Could not add ${got} imagery: ${(e as Error)?.message ?? e}`);
      }
    }
    sync();
  }

  resync = sync;
  sync();
  return root;
}
