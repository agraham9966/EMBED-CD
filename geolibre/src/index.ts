import type { GeoLibrePlugin } from "./host-api";
import "./style.css";

export const plugin: GeoLibrePlugin = {
  id: "embed-cd",
  name: "EMBED-CD",
  version: "0.1.0",
  engines: ["maplibre"],
  activate() {},
  deactivate() {},
};

export default plugin;
