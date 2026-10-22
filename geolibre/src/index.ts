import type { GeoLibrePlugin } from "./host-api";
import { wipeLegacyStorage } from "./legacy";
import { registerPanel } from "./panel";
import { sessionRuns } from "./runs";
import "./style.css";

let disposePanel: (() => void) | null = null;

export const plugin: GeoLibrePlugin = {
  id: "embed-cd",
  name: "EMBED-CD",
  version: "0.4.3",
  engines: ["maplibre"],
  activate(app) {
    void wipeLegacyStorage();             // once-only cleanup of what builds 0.1-0.2 stored
    disposePanel ??= registerPanel(app);   // a second activate must not add a second panel
  },
  deactivate() {
    sessionRuns.clearLayers();              // the plugin is off: take its layers with it
    disposePanel?.();
    disposePanel = null;
  },
};

export default plugin;
