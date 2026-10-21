import type { GeoLibrePlugin } from "./host-api";
import { registerPanel } from "./panel";
import "./style.css";

let disposePanel: (() => void) | null = null;

export const plugin: GeoLibrePlugin = {
  id: "embed-cd",
  name: "EMBED-CD",
  version: "0.1.0",
  engines: ["maplibre"],
  activate(app) {
    disposePanel ??= registerPanel(app);   // a second activate must not add a second panel
  },
  deactivate() {
    disposePanel?.();
    disposePanel = null;
  },
};

export default plugin;
