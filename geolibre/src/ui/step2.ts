// Step 2: the cutoff. Symbology only (instant, reversible), plus Auto (Otsu) and "% changed".
import { fractionAbove, otsu } from "../engine/score";
import { el, row } from "./dom";

export type Step2 = {
  root: HTMLElement;
  setHist(h: Float64Array | null): void;
  setCutoff(t: number): void;
  auto(): void;
  cutoff(): number;
};

export function buildStep2(onCutoff: (t: number) => void): Step2 {
  const root = el("section", "ecd-step");
  const slider = el("input", "ecd-slider") as HTMLInputElement;
  Object.assign(slider, { type: "range", min: "0", max: "1", step: "0.01", value: "0.15" });
  slider.title = "Cutoff: only change above this shows. Moving it is instant and changes nothing on disk.";
  const value = el("span", "ecd-value", "0.15");
  const autoBtn = el("button", "ecd-btn", "Auto") as HTMLButtonElement;
  autoBtn.title = "Pick the cutoff that best separates changed from unchanged (Otsu).";
  const changed = el("p", "ecd-muted", "");
  root.append(row(el("span", "", "Cutoff"), slider, value, autoBtn), changed);

  let hist: Float64Array | null = null;
  const show = (t: number) => {
    value.textContent = t.toFixed(2);
    changed.textContent = hist ? `${(fractionAbove(hist, t) * 100).toFixed(1)}% of the area changed at ${t.toFixed(2)}` : "";
  };
  const set = (t: number) => {
    slider.value = String(t);
    show(t);
    onCutoff(t);
  };
  slider.oninput = () => set(Number(slider.value));
  const api: Step2 = {
    root,
    setHist(h) {
      hist = h;
      autoBtn.disabled = slider.disabled = !h;
      show(Number(slider.value));
    },
    setCutoff: (t) => set(Math.round(t * 100) / 100),
    auto() {
      if (hist) set(Math.round(otsu(hist) * 100) / 100);
    },
    cutoff: () => Number(slider.value),
  };
  autoBtn.onclick = () => api.auto();
  api.setHist(null);
  return api;
}
