import type { GeoLibreAppAPI } from "./host-api";
import { TileIndex } from "./engine/tileindex";
import { startJob } from "./jobclient";
import { removeArea } from "./map/drawbox";
import { sessionRuns } from "./runs";
import { buildStep1 } from "./ui/step1";
import { buildStep2 } from "./ui/step2";
import { buildStep3 } from "./ui/step3";
import { Accordion } from "./ui/accordion";
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
      const acc = new Accordion();
      let step1: ReturnType<typeof buildStep1> | null = null;
      // The headers carry each step's answer, and the steps move forward once (as in QGIS).
      const refresh = () => {
        if (!step1) return;
        const has = step1.hasResult(), sum = step1.summary();
        acc.setTitles([sum ? `1 · ${sum}` : "1 · Area, years and output",
          has ? `2 · Change map, cutoff ${step2.cutoff().toFixed(2)}` : "2 · Change map",
          "3 · Objects and classes"]);
        acc.setEnabled([true, has, has]);
        if (has) acc.foldOnce("step1", 1);
      };
      const step2 = buildStep2((t) => { step1?.layer()?.setCutoff(t); refresh(); });
      const step3 = buildStep3({ app, runs: sessionRuns, currentRun: () => step1?.current() ?? null, cutoff: () => step2.cutoff(),
        // Through GeoLibre's own visibility (when the host has it), so the Layers panel's eye agrees.
        onObjectsMade: () => {
          const id = step1?.layer()?.changeId;
          if (id) { try { app.setLayerVisibility?.(id, false); } catch { /* layer already gone */ } }
          acc.foldOnce("step2", 2);                       // objects exist: on to classifying
        } });
      step1 = buildStep1({ app, runs: sessionRuns, step2, loadIndex: () => TileIndex.load(), startJob,
        onRunChanged: (id) => { step3.runChanged(id); refresh(); },
        onChange: refresh,
        onAreaDrawn: () => { acc.resetFold("step1"); acc.resetFold("step2"); acc.open(0); } });
      acc.add(step1.root);
      acc.add(step2.root);
      acc.add(step3.root);
      refresh();
      root.append(acc.root,
        el("p", "ecd-muted", "Runs are temporary: they are discarded when GeoLibre closes. Use Save run to keep one as a file."));
      return () => {
        step3.dispose();
        step1?.dispose();                                  // runs' layers stay on the map
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
