import type { GeoLibrePlugin } from "./host-api";
import { registerPanel } from "./panel";
import { clearPhotos } from "./ui/photos";
import { sessionRuns } from "./runs";
import css from "./style.css?inline";

// The panel's CSS as a constructed stylesheet, not the manifest's `style`. GeoLibre injects that as
// a <style> element, and desktop 3.5.0 blocks those: its index.html gained an inline <style>, Tauri
// adds that block's hash to the CSP's style-src, and with a hash present the browser ignores
// 'unsafe-inline' (every plugin's styles were dropped). A constructed sheet is not subject to it.
let sheet: CSSStyleSheet | null = null;
function addStyles(): void {
  if (sheet || typeof CSSStyleSheet === "undefined" || !("adoptedStyleSheets" in document)) return;
  sheet = new CSSStyleSheet();
  sheet.replaceSync(css);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}
function removeStyles(): void {
  if (!sheet) return;
  document.adoptedStyleSheets = document.adoptedStyleSheets.filter((s) => s !== sheet);
  sheet = null;
}

let disposePanel: (() => void) | null = null;
let host: Parameters<GeoLibrePlugin["activate"]>[0] | null = null;

export const plugin: GeoLibrePlugin = {
  id: "embed-cd",
  name: "EMBED-CD",
  version: "0.8.15",
  engines: ["maplibre"],
  activate(app) {
    host = app;
    addStyles();
    disposePanel ??= registerPanel(app);   // a second activate must not add a second panel
  },
  deactivate() {
    sessionRuns.clearLayers();              // the plugin is off: take its layers with it
    if (host) clearPhotos(host);
    disposePanel?.();
    disposePanel = null;
    removeStyles();
  },
};

export default plugin;
