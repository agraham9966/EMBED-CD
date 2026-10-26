// Step 3's classes and labelling controls, wired to the Labeller, the objects layer and the click
// tool. Mirrors the QGIS ClassifyPanel: class list with colours and counts, add / delete / rename,
// Inspect, Label by clicking, stepping, this object's class, Undo, Discard, the score breakdown,
// the options (mode, assign selected, pause, best guess, strictness) and class sets.
import { UNKNOWN } from "../engine/head";
import type { GeoLibreAppAPI } from "../host-api";
import { Labeller, type CycleMode, type LabelState } from "../labeller";
import { ClickTool } from "../map/clicktool";
import type { ObjectsLayer } from "../map/objectslayer";
import { saveText } from "../export";
import { el, row, select, withIcon } from "./dom";

const ARROW = " → ";
const CYCLES: [CycleMode, string][] = [["uncertain", "Least certain first"], ["unlabelled", "Only unlabelled"],
  ["unknown", "Only unknown"], ["labelled", "Only labelled"], ["class", "Only the selected class"], ["all", "All objects"]];
const BAR = 13;


export type ClassifyUi = {
  root: HTMLElement;
  /** Objects for a run were made or shown: classify them (labels on earlier objects carry over). */
  setObjects(vectors: Float32Array, n: number, layer: ObjectsLayer, state?: LabelState): void;
  clear(): void;
  /** The panel is going: switch the click tool off. */
  dispose(): void;
  status(): string;
  labeller: Labeller;
};

export function buildClassify(app: GeoLibreAppAPI, setStatus: (s: string) => void, onChange?: (state: LabelState) => void): ClassifyUi {
  const L = new Labeller();
  let layer: ObjectsLayer | null = null;
  let current: number | null = null;                // the object the panel is talking about
  let cls: string | null = null;                     // the class you label with
  let cycleAt = -1, filling = false;

  const root = el("div", "ecd-classify");
  const list = el("div", "ecd-classes");
  const btn = (text: string, title: string) => { const b = el("button", "ecd-btn ecd-small", text) as HTMLButtonElement; b.title = title; return b; };
  const addB = withIcon(btn("Add", "Add a class"), "add"), delB = withIcon(btn("Delete", "Delete the selected class"), "delete");
  const renB = withIcon(btn("Rename", "Rename the selected class"), "rename");
  const inspectB = withIcon(btn("Inspect", "Inspect: click a polygon to see the model's scores for it, without labelling it."), "inspect");
  const optsB = withIcon(btn("Options", "Classifier options: mode, bulk assign, pause, best guess, strictness."), "options");
  const setsB = withIcon(btn("Class set", "Save or load a class set, to reuse it on another area."), "classset");
  const nameForm = el("div", "ecd-nameform");
  const labelB = withIcon(el("button", "ecd-btn ecd-toggle", "Label by clicking the map") as HTMLButtonElement, "label");
  labelB.title = "Turn this on, then click polygons on the map to put them in the selected class. Right-click removes a label.";
  const cycle = select(CYCLES.map(([k, l]) => [k, l]));
  const prevB = withIcon(btn("", "Step to the previous object in the list. The map pans to it and keeps your zoom."), "prev", "Previous object");
  const nextB = withIcon(btn("", "Step to the next object in the list. The map pans to it and keeps your zoom."), "next", "Next object");
  const objCls = select([]);
  objCls.title = "The class of the object you are looking at. Changing it relabels that object.";
  const undoB = withIcon(btn("Undo", "Undo the last labelling action. A bulk assign comes back as one step."), "undo");
  const discardB = withIcon(btn("Discard", "Remove this object's label."), "discard");
  const detail = el("div", "ecd-detail");

  // Options (QGIS keeps these behind the gear: real, but rarely touched mid-session).
  const opts = el("div", "ecd-options");
  opts.hidden = true;
  const mode = select([["after", "End state — what it is now"], ["delta", "Transition — what changed"]], "after");
  mode.title = "What a class means. Transition: what happened here. End state: what it is now, and carries to a different landscape.";
  const assignB = el("button", "ecd-btn", "Assign the selected polygons to this class") as HTMLButtonElement;
  const pauseC = Object.assign(el("input") as HTMLInputElement, { type: "checkbox" });
  const guessC = Object.assign(el("input") as HTMLInputElement, { type: "checkbox" });
  const strict = Object.assign(el("input", "ecd-slider") as HTMLInputElement, { type: "range", min: "0", max: "40", step: "1", value: "5" });
  strict.title = "How sure a class has to be before it claims an object. Higher leaves more objects unknown.";
  const strictV = el("span", "ecd-value", "0.05");
  const lab = (input: HTMLElement, text: string) => { const l = el("label", "ecd-check"); l.append(input, " " + text); return l; };
  opts.append(row(el("span", "", "Classify by"), mode), assignB, lab(pauseC, "Pause the classifier, my edits only"),
    lab(guessC, "Prefer a best guess over 'unknown'"), row(el("span", "", "Strictness"), strict, strictV));
  const sets = el("div", "ecd-options");
  sets.hidden = true;
  const saveSetB = el("button", "ecd-btn", "Save classes...") as HTMLButtonElement;
  const loadSetB = el("button", "ecd-btn", "Load classes...") as HTMLButtonElement;
  sets.append(el("p", "ecd-muted", "A class set is just your labelled examples: save it and the same classes can be applied to another area."), row(saveSetB, loadSetB));

  // What to do next, in one line, with the button it means lit up: after Generate the panel is a
  // wall of equal-looking buttons and nothing says where to start.
  const guide = el("div", "ecd-guide");

  root.append(guide, list, row(addB, delB, renB, inspectB, optsB, setsB), nameForm, opts, sets, labelB,
    row(el("span", "", "Step through"), cycle, prevB, nextB), row(el("span", "", "This object"), objCls, undoB, discardB), detail);

  const tool = new ClickTool(app, () => layer?.fillLayerId ?? null, (r, right) => clicked(r, right));

  // ---------------- drawing the state ----------------
  function render(): void {
    const here = L.labelledCounts(), carried = L.carriedCounts(), pred = L.predictedCounts();
    list.replaceChildren(...L.classes.map((name) => {
      const r = el("div", "ecd-class" + (name === cls ? " ecd-selected" : ""));
      const sw = Object.assign(el("input", "ecd-swatch") as HTMLInputElement, { type: "color", value: L.colors[name] ?? "#888780" });
      sw.title = "Change this class's colour.";
      sw.onclick = (e) => e.stopPropagation();
      sw.oninput = () => { L.colors[name] = sw.value; paint(); };
      const c = carried[name] ?? 0;
      const taught = c ? `${here[name] ?? 0} labelled here + ${c} carried over` : `${here[name] ?? 0} labelled`;
      const text = el("span", "", `${name} — ${taught}, ${pred[name] ?? 0} predicted`);
      if (c) r.title = "Carried over: examples you labelled on an earlier run or area, or loaded from a class set. They still train the classifier here; label a few objects in this area to adapt it.";
      r.append(sw, text);
      r.onclick = () => { cls = name; cycleAt = -1; render(); };
      r.ondblclick = () => { cls = name; cycle.value = "class"; cycleAt = -1; render(); step(1); };
      return r;
    }));
    if (pred[UNKNOWN]) list.append(el("div", "ecd-class ecd-unknown", `unknown — ${pred[UNKNOWN]} predicted`));
    const has = !!layer && L.objectCount > 0;
    mode.value = L.features === "delta" ? "delta" : "after";
    for (const b of [labelB, inspectB, prevB, nextB, cycle, discardB, assignB, mode, saveSetB]) (b as HTMLButtonElement).disabled = !has;
    undoB.disabled = !L.canUndo;
    labelB.classList.toggle("ecd-on", tool.isOn && !inspecting);
    inspectB.classList.toggle("ecd-on", tool.isOn && inspecting);
    const classOpt = cycle.querySelector("option[value=class]") as HTMLOptionElement;
    classOpt.textContent = cls ? `Only "${cls}"` : "Only the selected class";
    classOpt.disabled = !cls;
    fillObjectCombo();
    showDetail();
    showGuide(has, here);
  }

  function showGuide(has: boolean, here: Record<string, number>): void {
    for (const b of [addB, labelB, nextB]) b.classList.remove("ecd-next");
    guide.hidden = !has;
    if (!has) return;
    const n = Object.values(here).reduce((a, b) => a + b, 0);
    const carried = Object.values(L.carriedCounts()).reduce((a, b) => a + b, 0);
    let text: string, next: HTMLButtonElement | null = null;
    if (!L.classes.length) {
      text = "Click Add and name the kind of change you are after, for example 'Clearcut'.";
      next = addB;
    } else if (tool.isOn && inspecting) {
      text = "Click an object on the map to see how the classifier scores it. Click Inspect again to stop.";
    } else if (!tool.isOn && n === 0) {
      text = carried
        ? `Classes carried over from earlier runs are already colouring the map. Turn on Label by clicking the map and fix any it got wrong.`
        : `Turn on Label by clicking the map, then click a few objects that are '${cls}'.`;
      next = labelB;
    } else if (n < 3) {
      text = `Click objects on the map that are '${cls}'. ${n} labelled so far. Right-click removes a label.`;
      if (!tool.isOn) next = labelB;
    } else if (L.classes.length === 1) {
      text = "The rest are coloured on the map. Now add a second class for what you are NOT after (for example 'Other') "
        + "and label a few: telling two apart works far better than one alone.";
      next = addB;
    } else {
      text = "Check its calls: step through the least certain objects with ▶ and fix any it got wrong. "
        + "Save the classes (Class set) to reuse them on another area.";
      next = nextB;
    }
    guide.replaceChildren(el("b", "", "Next: "), text);
    next?.classList.add("ecd-next");
  }

  function paint(): void {
    layer?.update(L.pred, L.labels, L.colors, L.conf);
    layer?.highlight(current);
    if (layer) onChange?.(L.snapshot());             // every change lands here: keep it with the run
  }

  function changed(note?: string): void {
    paint();
    render();
    setStatus(note ? `${note}. ${L.status}` : L.status);
  }

  function fillObjectCombo(): void {
    filling = true;
    objCls.replaceChildren(new Option("— no label —", ""), ...L.classes.map((c) => new Option(c, c)));
    objCls.disabled = current === null;
    if (current === null) objCls.options[0].text = "— no object selected —";
    else {
      const mine = L.labels.get(current);
      if (!mine && L.pred) objCls.options[0].text = `— unlabelled (${L.pred[current] || "unknown"} predicted) —`;
      objCls.value = mine ?? "";
    }
    filling = false;
  }

  function showDetail(): void {
    detail.replaceChildren();
    if (current === null || !L.head || !L.scores) return;
    const n = L.objectCount, props: any = layer?.feature(current)?.properties ?? {};
    const col = L.head.classes.map((c, k) => [c, L.scores![k * n + current!]] as const).sort((a, b) => b[1] - a[1]);
    const mine = L.labels.get(current);
    const headLine = el("div", "ecd-detail-head");
    headLine.append(el("b", "", `${(props.area_ha ?? 0).toFixed(1)} ha`), ` · change ${(props.chg_mean ?? 0).toFixed(3)} · `,
      mine ? "you labelled this " : "called ", el("b", "", mine ?? L.pred?.[current] ?? ""));
    detail.append(headLine);
    for (const [name, v] of col) {
      const fires = v >= (L.head.thr[name] ?? 1);
      const filled = Math.max(0, Math.min(BAR, Math.round(v * BAR)));
      const line = el("div", "ecd-bar", `${"█".repeat(filled)}${"·".repeat(BAR - filled)} ${v.toFixed(2)} ${name}`);
      line.style.color = fires ? (L.colors[name] ?? "#888780") : "#8a8a8a";
      line.style.fontWeight = fires ? "600" : "400";
      detail.append(line);
    }
    detail.append(el("div", "ecd-muted", `${L.head.singleClass ? "similarity" : "confidence"}; bold cleared its own bar`));
  }

  // ---------------- actions ----------------
  let inspecting = false;
  function clicked(r: number | null, right: boolean): void {
    if (r === null) { setStatus("No polygon there."); return; }
    current = r;
    if (inspecting) {
      paint(); render();
      const mine = L.labels.get(r);
      setStatus(mine ? `you labelled this '${mine}'.` : `the model calls this '${L.pred?.[r] || "nothing yet"}'.`);
      return;
    }
    if (!right && !cls) { setStatus("Pick a class in the list first."); return; }
    L.label([r], right ? null : cls);
    changed(right ? "label removed" : `labelled '${cls}'`);
  }

  labelB.onclick = () => {
    if (tool.isOn && !inspecting) { tool.off(); render(); setStatus(""); return; }
    if (!L.classes.length) { setStatus("Add a class first, then click polygons to fill it."); return; }
    cls ??= L.classes[0];
    inspecting = false;
    tool.on();
    render();
    setStatus(`Click polygons to label them '${cls}'. Right-click removes a label.`);
  };
  inspectB.onclick = () => {
    if (tool.isOn && inspecting) { tool.off(); inspecting = false; render(); setStatus(""); return; }
    inspecting = true;
    tool.on("inspect");
    render();
    setStatus("Click a polygon to see what the classifier makes of it. Nothing is labelled in this mode.");
  };

  function askName(title: string, initial: string, done: (name: string) => void): void {
    const transition = L.features === "delta";
    const [before, after] = initial.includes(ARROW.trim()) ? initial.split(ARROW.trim()).map((s) => s.trim()) : [initial, ""];
    const a = Object.assign(el("input", "ecd-text") as HTMLInputElement, { value: before, placeholder: transition ? "forest" : "name" });
    const b = Object.assign(el("input", "ecd-text") as HTMLInputElement, { value: after, placeholder: "clearing   (optional)" });
    const ok = el("button", "ecd-btn", "OK") as HTMLButtonElement, cancel = el("button", "ecd-btn", "Cancel") as HTMLButtonElement;
    nameForm.replaceChildren(el("div", "ecd-h", title), transition ? row(el("span", "", "From"), a) : row(el("span", "", "Name"), a));
    if (transition) nameForm.append(row(el("span", "", "To"), b), el("p", "ecd-muted", "A class is a transition, so name both ends. Leave 'To' empty for a plain name."));
    nameForm.append(row(ok, cancel));
    const finish = () => nameForm.replaceChildren();
    ok.onclick = () => {
      const x = a.value.trim(), y = b.value.trim();
      finish();
      const name = transition && x && y ? `${x}${ARROW}${y}` : x || y;
      if (name) done(name);
    };
    cancel.onclick = finish;
    a.onkeydown = b.onkeydown = (e) => { if (e.key === "Enter") ok.click(); if (e.key === "Escape") finish(); };
    a.focus();
  }

  addB.onclick = () => askName("Add class", "", (name) => {
    if (L.addClass(name)) { cls = name; changed(); }
  });
  renB.onclick = () => {
    if (!cls) return;
    const old = cls;
    askName("Rename class", old, (name) => { if (L.renameClass(old, name)) { cls = name; changed(); } });
  };
  delB.onclick = () => {
    if (!cls) return;
    L.deleteClass(cls);
    cls = L.classes[0] ?? null;
    changed();
  };
  optsB.onclick = () => { opts.hidden = !opts.hidden; sets.hidden = true; };
  setsB.onclick = () => { sets.hidden = !sets.hidden; opts.hidden = true; };

  function step(delta: number): void {
    const rows = L.cycleRows(cycle.value as CycleMode, cls);
    if (!rows.length) { setStatus("Nothing to step through with this filter."); return; }
    cycleAt = cycleAt < 0 || cycleAt >= rows.length ? (delta > 0 ? 0 : rows.length - 1) : (cycleAt + delta + rows.length) % rows.length;
    goTo(rows[cycleAt]);
    setStatus(`${cycleAt + 1} of ${rows.length}  (${cycle.options[cycle.selectedIndex].text.toLowerCase()})`);
  }
  prevB.onclick = () => step(-1);
  nextB.onclick = () => step(1);
  cycle.onchange = () => { cycleAt = -1; };

  /** Pan to an object, keeping the zoom unless it does not fit (QGIS _goto_row). */
  function goTo(r: number): void {
    current = r;
    paint();
    render();
    const map = (app as any).getMap?.();
    const coords: [number, number][] = (layer?.feature(r)?.geometry as any)?.coordinates?.[0] ?? [];
    if (!map || !coords.length) return;
    const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1]);
    const box: [[number, number], [number, number]] = [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
    const b = map.getBounds?.();
    const fits = b && box[1][0] - box[0][0] <= b.getEast() - b.getWest() && box[1][1] - box[0][1] <= b.getNorth() - b.getSouth();
    if (fits) map.easeTo({ center: [(box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2], duration: 300 });
    else map.fitBounds(box, { padding: 40, duration: 300 });
  }

  objCls.onchange = () => {
    if (filling || current === null) return;
    const name = objCls.value || null;
    if (name === (L.labels.get(current) ?? null)) return;
    L.label([current], name);
    changed();
  };
  undoB.onclick = () => { L.undo(); paint(); render(); setStatus(L.status); };
  discardB.onclick = () => {
    if (current === null || !L.labels.has(current)) { setStatus("No labelled polygon selected."); return; }
    L.label([current], null);
    changed("Removed 1 label");
  };
  assignB.onclick = () => {
    if (!cls) { setStatus("Pick a class first (or add one)."); return; }
    const sel = (app as any).getSelectedLayerId?.() === layer?.id ? ((app as any).getSelectedFeatures?.() ?? []) : [];
    const rows = sel.map((f: any) => Number(f.properties?.id) - 1).filter((r: number) => r >= 0);
    if (!rows.length) { setStatus("Select one or more polygons on the map first (GeoLibre's select tool, on the Objects layer)."); return; }
    L.label(rows, cls);
    changed(`${rows.length} labelled '${cls}'`);
  };
  mode.onchange = () => { L.setFeatures(mode.value as "after" | "delta"); changed(); };
  pauseC.onchange = () => {
    L.setPaused(pauseC.checked);
    for (const w of [strict, guessC, mode]) w.disabled = pauseC.checked;
    changed();
  };
  guessC.onchange = () => { L.setBestGuess(guessC.checked); changed(); };
  strict.oninput = () => { strictV.textContent = (Number(strict.value) / 100).toFixed(2); };
  strict.onchange = () => { L.setQ(Number(strict.value) / 100); changed(); };
  saveSetB.onclick = () => saveText(app, "embed-cd-classes.json", L.saveClassSet(), { description: "EMBED-CD class set", extensions: ["json"], mimeType: "application/json" });
  loadSetB.onclick = async () => {
    const text = await pickText();
    if (!text) return;
    const note = L.loadClassSet(text);
    mode.value = L.features === "delta" ? "delta" : "after";
    cls = L.classes[0] ?? null;
    paint(); render();
    setStatus(L.status || note);
  };

  async function pickText(): Promise<string | null> {
    if ((app as any).importTextFile) return (app as any).importTextFile({ description: "EMBED-CD class set", extensions: ["json"] });
    return new Promise((resolve) => {
      const input = Object.assign(el("input") as HTMLInputElement, { type: "file", accept: ".json" });
      input.onchange = async () => resolve(input.files?.[0] ? await input.files[0].text() : null);
      input.click();
    });
  }

  render();
  return {
    root,
    labeller: L,
    setObjects(vectors, n, l, state) {
      layer = l;
      current = null;
      cycleAt = -1;
      L.setObjects(vectors, n);
      if (state) L.restore(state);
      cls ??= L.classes[0] ?? null;
      paint();
      render();
    },
    clear() {
      tool.off();
      layer = null;
      current = null;
      render();
    },
    dispose() {
      tool.off();
    },
    status: () => L.status,
  };
}
