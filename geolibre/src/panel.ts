import type { GeoLibreAppAPI } from "./host-api";
import { renderSpike } from "./spike";

export const PANEL_ID = "embed-cd-panel";

/** Register and open the EMBED-CD right panel; returns a disposer that closes and removes it. */
export function registerPanel(app: GeoLibreAppAPI): () => void {
  if (!app.registerRightPanel) return () => {};
  const unregister = app.registerRightPanel({
    id: PANEL_ID,
    title: "EMBED-CD",
    defaultWidth: 340,
    render(container: HTMLElement) {
      const root = document.createElement("div");
      root.className = "ecd-panel";
      const intro = document.createElement("p");
      intro.className = "ecd-muted";
      intro.textContent = "Change maps from satellite embeddings. Early build: the tools arrive in later versions.";
      root.append(intro);
      container.append(root);
      const disposeSpike = renderSpike(root, app);
      return () => {
        disposeSpike();
        root.remove();
      };
    },
  });
  app.openRightPanel?.(PANEL_ID);
  return () => {
    app.closeRightPanel?.(PANEL_ID);
    unregister();
  };
}
