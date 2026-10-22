import type { GeoLibreAppAPI } from "./host-api";
import { TileIndex } from "./engine/tileindex";
import { startJob } from "./jobclient";
import { removeArea } from "./map/drawbox";
import { sessionRuns } from "./runs";
import { buildStep1 } from "./ui/step1";
import { buildStep2 } from "./ui/step2";
import { el } from "./ui/dom";

export const PANEL_ID = "embed-cd-panel";

/** Register and open the EMBED-CD right panel; returns a disposer that closes and removes it. */
export function registerPanel(app: GeoLibreAppAPI): () => void {
  if (!app.registerRightPanel) return () => {};
  const unregister = app.registerRightPanel({
    id: PANEL_ID,
    title: "EMBED-CD",
    defaultWidth: 340,
    render(container: HTMLElement) {
      const root = el("div", "ecd-panel");
      container.append(root);
      let step1: ReturnType<typeof buildStep1> | null = null;
      const step2 = buildStep2((t) => step1?.layer()?.setCutoff(t));
      step1 = buildStep1({ app, runs: sessionRuns, step2, loadIndex: () => TileIndex.load(), startJob });
      root.append(step1.root, step2.root,
        el("p", "ecd-muted", "Runs are temporary: they are discarded when GeoLibre closes. Use Save run to keep one as a file."));
      return () => {
        step1?.dispose();
        step1?.layer()?.clear();
        removeArea((app as any).getMap?.());
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
