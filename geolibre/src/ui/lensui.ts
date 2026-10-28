// The before/after lens and its settings, in the Reference imagery section: it brings its own
// imagery and is useful at every step, not only while labelling. The shortcut key works anywhere
// outside a text box while the panel is open.
import type { FeatureCollection } from "geojson";
import type { GeoLibreAppAPI } from "../host-api";
import { EOX_YEARS, nearestYear } from "../map/eox";
import { CompareLens, editable, MAX_R, MIN_R } from "../map/lens";
import { el, row, select, withIcon } from "./dom";

// The lens's settings, for this session only (nothing is stored on disk): kept here so they
// survive the panel being closed and reopened.
const lensPrefs = { on: false, size: 100, before: "", after: "", key: "L" };

export type LensUi = {
  root: HTMLElement;
  /** The objects of the run on screen, drawn in the lens too (null: none). */
  setOutlines(fc: FeatureCollection | null): void;
  /** The run on screen changed: Auto follows its years. */
  sync(): void;
  dispose(): void;
};

/** `runYears`: the years of the change map on screen, for the lens's Auto. */
export function buildLens(app: GeoLibreAppAPI, runYears: () => [number, number] | null = () => null): LensUi {
  const root = el("div", "ecd-lens-ui");
  const note = el("p", "ecd-muted", "");
  note.hidden = true;
  const setStatus = (s: string) => { note.textContent = s; note.hidden = !s; };
  const smallBtn = (title: string) => { const b = el("button", "ecd-btn ecd-small") as HTMLButtonElement; b.title = title; return b; };
  const lensB = withIcon(el("button", "ecd-btn ecd-small ecd-toggle", "Before/after lens") as HTMLButtonElement, "lens");
  const lensHint = el("span", "ecd-keyhint");
  lensB.append(lensHint);
  lensB.title = "Hover the map to see the before year's imagery under the cursor; hold A to see the after year. "
    + "Shift+scroll resizes it.\n\nBrings its own imagery (Sentinel-2 cloudless, EOX), so no basemap needs to be open. "
    + "Clicks still label objects as usual. The arrow sets the years, size and shortcut key.";
  const lensSetB = withIcon(smallBtn("Lens years, size and shortcut key."), "more", "Lens settings");
  const lensOpts = el("div", "ecd-options");
  lensOpts.hidden = true;
  const yearPick = (v: string) => {
    const s = select([["", "Auto"], ...EOX_YEARS.map((y) => [String(y), String(y)] as [string, string])], v);
    s.title = "Auto follows the current change map's years. Pick a year to override it.";
    return s;
  };
  const lensBefore = yearPick(lensPrefs.before), lensAfter = yearPick(lensPrefs.after);
  const lensSize = Object.assign(el("input", "ecd-number") as HTMLInputElement,
    { type: "number", min: String(MIN_R), max: String(MAX_R), step: "10", value: String(lensPrefs.size) });
  const lensKey = Object.assign(el("input", "ecd-number ecd-key") as HTMLInputElement, { maxLength: 1, value: lensPrefs.key });
  lensKey.title = "Press this key anywhere outside a text box to turn the lens on or off. Leave it empty for no shortcut. "
    + "A is taken: holding it shows the after year.";
  lensOpts.append(row(el("span", "", "Before"), lensBefore), row(el("span", "", "After"), lensAfter),
    row(el("span", "", "Size"), lensSize, el("span", "", "px radius")),
    row(el("span", "", "Shortcut"), lensKey, el("span", "", "turns the lens on and off")));
  const lens = new CompareLens(app);
  lens.setRadius(lensPrefs.size);

  root.append(row(lensB, lensSetB), lensOpts, note);

  /** The EOX years to show, Auto resolved; the Auto entries say what they picked. */
  let lastRun: [number, number] = [2019, 2024];      // a new area with no run yet keeps the last years
  function syncLens(): void {
    const run = (lastRun = runYears() ?? lastRun);
    const [b, a] = ([[lensBefore, run[0]], [lensAfter, run[1]]] as [HTMLSelectElement, number][]).map(([s, y]) => {
      const got = nearestYear(y);
      s.options[0].text = got === y ? `Auto (${got})` : `Auto (${got}, nearest to ${y})`;
      return s.value ? Number(s.value) : got;
    });
    lens.setYears(b, a);
  }
  function setLens(on: boolean): void {
    syncLens();
    lens.setActive(on);
    if (on && !lens.active) setStatus(lens.error || "The lens could not start.");
    lensPrefs.on = lens.active;
    lensB.classList.toggle("ecd-on", lens.active);
  }
  lensB.onclick = () => setLens(!lens.active);
  lensSetB.onclick = () => { lensOpts.hidden = !lensOpts.hidden; };
  lensBefore.onchange = lensAfter.onchange = () => {
    lensPrefs.before = lensBefore.value;
    lensPrefs.after = lensAfter.value;
    syncLens();
  };
  lensSize.onchange = () => { lens.setRadius(Number(lensSize.value) || 100); lensPrefs.size = lens.radius; lensSize.value = String(lens.radius); };
  lens.onRadiusChanged = (r) => { lensPrefs.size = r; lensSize.value = String(r); };
  lensKey.oninput = () => {
    const k = lensKey.value.trim().toUpperCase();
    lensKey.value = k === "A" ? "" : k;                 // A shows the after year
    lensPrefs.key = lensKey.value;
    showKey();
    if (k === "A") setStatus("A is taken by the lens itself (hold it to see the after year). Pick another key.");
  };
  function showKey(): void {
    lensHint.textContent = lensPrefs.key;
    lensHint.hidden = !lensPrefs.key;
    lensHint.title = `Shortcut: ${lensPrefs.key}`;
  }
  showKey();
  // Capture, so the key reaches us before a GeoLibre shortcut; only claimed when it is ours.
  const onLensKey = (e: KeyboardEvent) => {
    if (!lensPrefs.key || e.repeat || e.ctrlKey || e.metaKey || e.altKey || editable(e.target)) return;
    if (e.key.toUpperCase() !== lensPrefs.key) return;
    e.preventDefault();
    e.stopPropagation();
    setLens(!lens.active);
  };
  window.addEventListener("keydown", onLensKey, true);
  syncLens();
  if (lensPrefs.on) setTimeout(() => setLens(true), 0);

  return {
    root,
    setOutlines: (fc) => lens.setOutlines(fc),
    sync: syncLens,
    dispose() {
      lens.dispose();
      window.removeEventListener("keydown", onLensKey, true);
    },
  };
}
