// The labelling loop without any DOM: classes, colours, labels, examples carried over from
// earlier objects, undo, pause, and the refit that classifies everything. Ported from the QGIS
// ClassifyPanel (plugin/embed_cd_qgis/classify.py) so the two plugins behave the same.
import { fitFromClasses, loadClassesJson, reviewOrder, saveClassesJson, UNKNOWN, type Features, type OvRHead } from "./engine/head";

export const PALETTE = ["#d85a30", "#1d9e75", "#7f77dd", "#d4537e", "#378add", "#ba7517", "#639922", "#993556"];
export type CycleMode = "uncertain" | "unlabelled" | "unknown" | "labelled" | "class" | "all";

/** A run's labelling progress as plain data (QGIS writes the same to labels_<A>-<B>.json). */
export type LabelState = {
  classes: string[]; colors: Record<string, string>; banked: Record<string, number[][]>;
  labels: [number, string][]; features: Features;
};

export class Labeller {
  classes: string[] = [];
  colors: Record<string, string> = {};
  labels = new Map<number, string>();               // row -> class, for the CURRENT objects
  banked: Record<string, Float32Array[]> = {};       // examples from earlier objects or a loaded set
  features: Features = "after";                       // the panel opens on End state, as in QGIS
  q = 0.05;
  bestGuess = false;
  paused = false;
  head: OvRHead | null = null;
  pred: string[] | null = null;
  scores: Float64Array | null = null;
  conf: Float64Array | null = null;
  status = "";
  private vectors: Float32Array | null = null;
  private n = 0;
  private undoStack: [number, string | undefined][][] = [];

  get objectCount(): number { return this.n; }
  get canUndo(): boolean { return this.undoStack.length > 0; }
  vector(row: number): Float32Array { return this.vectors!.subarray(row * 128, row * 128 + 128); }

  /** New objects (a re-cut or another area). Current labels become carried examples first. */
  setObjects(vectors: Float32Array, n: number): void {
    this.bank();
    this.vectors = vectors;
    this.n = n;
    this.pred = this.scores = this.conf = null;
    this.refit();
  }

  private bank(): void {
    if (this.vectors) {
      for (const [row, name] of this.labels) (this.banked[name] ??= []).push(this.vector(row).slice());
    }
    this.labels = new Map();
    this.undoStack = [];                               // the rows they pointed at no longer exist
  }

  /** Every example: carried ones plus those labelled on the current objects. */
  classVectors(): Record<string, Float32Array[]> {
    const out: Record<string, Float32Array[]> = {};
    for (const [k, v] of Object.entries(this.banked)) if (v.length) out[k] = [...v];
    if (this.vectors) for (const [row, name] of this.labels) (out[name] ??= []).push(this.vector(row));
    return out;
  }

  addClass(name: string): boolean {
    name = name.trim();
    if (!name || this.classes.includes(name)) return false;
    this.classes.push(name);
    this.colors[name] ??= PALETTE[(this.classes.length - 1) % PALETTE.length];
    return true;
  }

  renameClass(old: string, name: string): boolean {
    name = name.trim();
    if (!name || name === old || this.classes.includes(name) || !this.classes.includes(old)) return false;
    this.classes[this.classes.indexOf(old)] = name;
    this.colors[name] = this.colors[old];
    delete this.colors[old];
    if (this.banked[old]) { this.banked[name] = this.banked[old]; delete this.banked[old]; }
    for (const [r, v] of this.labels) if (v === old) this.labels.set(r, name);
    this.refit();
    return true;
  }

  deleteClass(name: string): void {
    this.classes = this.classes.filter((c) => c !== name);
    delete this.colors[name];
    delete this.banked[name];                          // or it returns on the next fit
    for (const [r, v] of [...this.labels]) if (v === name) this.labels.delete(r);
    this.refit();
  }

  /** Label rows (null removes their labels). One undo step however many rows. */
  label(rows: number[], name: string | null): void {
    this.undoStack.push(rows.map((r) => [r, this.labels.get(r)]));
    this.undoStack.splice(0, Math.max(0, this.undoStack.length - 100));
    for (const r of rows) {
      if (name === null) this.labels.delete(r);
      else this.labels.set(r, name);
    }
    if (this.paused) this.applyManual(rows); else this.refit();
  }

  undo(): void {
    const batch = this.undoStack.pop();
    if (!batch) { this.status = "Nothing to undo."; return; }
    for (const [r, before] of batch) {
      if (before === undefined) this.labels.delete(r); else this.labels.set(r, before);
    }
    if (this.paused) this.applyManual(batch.map(([r]) => r)); else this.refit();
    this.status = `Undid ${batch.length} label${batch.length !== 1 ? "s" : ""}. Unlabelled objects go back to the classifier's own answer.`;
  }

  setQ(q: number): void { this.q = q; this.refit(); }
  setBestGuess(on: boolean): void { this.bestGuess = on; this.refit(); }
  setFeatures(f: Features): void { this.features = f; this.refit(); }
  setPaused(on: boolean): void {
    this.paused = on;
    if (on) this.status = "Paused. Labels now apply to just that polygon; nothing else will move. Untick to let it learn from them.";
    else this.refit();
  }

  /** Refit and reclassify everything. A user's own label is the last word on its object. */
  refit(force = false): void {
    if (!this.vectors || (this.paused && !force)) return;
    const classes = this.classVectors();
    if (!Object.keys(classes).length) {
      this.head = null;
      this.pred = this.scores = this.conf = null;
      this.status = "Add a class, then click polygons on the map to label them.";
      return;
    }
    try {
      this.head = fitFromClasses(classes, this.vectors, {
        abstainQuantile: this.q, decision: this.bestGuess ? "argmax" : "threshold", features: this.features,
      });
      const { pred, scores } = this.head!.predict(this.vectors, this.n);
      this.conf = this.head!.confidence(this.vectors, this.n);
      for (const [r, name] of this.labels) if (r < pred.length) pred[r] = name;
      this.pred = pred;
      this.scores = scores;
    } catch (e) {
      this.status = `Could not fit: ${(e as Error)?.message ?? e}`;
      return;
    }
    this.status = this.report();
  }

  private applyManual(rows: number[]): void {
    this.pred ??= new Array<string>(this.n).fill("");
    for (const r of rows) if (r < this.n) this.pred[r] = this.labels.get(r) ?? "";
    this.status = this.report();
  }

  report(): string {
    if (!this.pred) return "";
    const counts: Record<string, number> = {};
    for (const p of this.pred) counts[p] = (counts[p] ?? 0) + 1;
    const unknown = counts[UNKNOWN] ?? 0;
    delete counts[UNKNOWN];
    const parts = Object.keys(counts).sort().map((k) => `${counts[k]} ${k}`).join(", ");
    const mode = this.head?.singleClass ? " (ranked by similarity)" : "";
    return `${parts || "nothing classified"}${mode}${unknown ? ` · ${unknown} to review` : ""}`;
  }

  labelledCounts(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const v of this.labels.values()) out[v] = (out[v] ?? 0) + 1;
    return out;
  }

  carriedCounts(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(this.banked)) if (v.length) out[k] = v.length;
    return out;
  }

  predictedCounts(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const p of this.pred ?? []) if (p) out[p] = (out[p] ?? 0) + 1;
    return out;
  }

  /** The count line under the objects, as the QGIS panel words it. */
  countLine(nObjects: number): string {
    const carried = Object.values(this.carriedCounts()).reduce((a, b) => a + b, 0);
    return `${nObjects} objects · ${this.labels.size} labelled here` + (carried ? ` · ${carried} examples carried over from earlier runs` : "");
  }

  /** The rows the step arrows walk, per filter. */
  cycleRows(mode: CycleMode, cls: string | null): number[] {
    const all = Array.from({ length: this.n }, (_, i) => i);
    if (mode === "uncertain") {
      if (!this.head || !this.scores || !this.pred) return all;
      const locked = all.map((i) => this.labels.has(i));
      return reviewOrder(this.pred, this.scores, this.head.classes.length, this.n, locked);
    }
    if (mode === "unlabelled") return all.filter((i) => !this.labels.has(i));
    if (mode === "unknown") {
      if (!this.pred) return all;
      return all.filter((i) => (this.pred![i] === UNKNOWN || this.pred![i] === "") && !this.labels.has(i));
    }
    if (mode === "labelled") return [...this.labels.keys()].sort((a, b) => a - b);
    if (mode === "class") {
      if (!cls) return [];
      return all.filter((i) => this.labels.get(i) === cls || (!this.labels.has(i) && this.pred?.[i] === cls));
    }
    return all;
  }

  snapshot(): LabelState {
    const banked: Record<string, number[][]> = {};
    for (const [k, v] of Object.entries(this.banked)) banked[k] = v.map((r) => Array.from(r));
    return { classes: [...this.classes], colors: { ...this.colors }, banked, labels: [...this.labels.entries()], features: this.features };
  }

  /** Bring back a run's own labelling (classify.py restore): its classes, colours, carried
   *  examples and labels replace what was carried here, so nothing is counted twice. */
  restore(st: LabelState): void {
    this.classes = [...st.classes];
    Object.assign(this.colors, st.colors);
    this.banked = {};
    for (const [k, v] of Object.entries(st.banked)) this.banked[k] = v.map((r) => Float32Array.from(r));
    this.labels = new Map(st.labels.filter(([r]) => r < this.n));
    this.features = st.features;
    this.undoStack = [];
    this.refit(true);
  }

  saveClassSet(): string {
    return saveClassesJson(this.classVectors(), this.colors, this.features);
  }

  /** Load a class set (as QGIS writes them). Adopts the file's mode; returns the status note. */
  loadClassSet(text: string): string {
    const { classes, colors, features } = loadClassesJson(text);
    const first = Object.values(classes).find((v) => v.length)?.[0];
    if (first && this.vectors && first.length !== 128) {
      return "These classes were saved from a different embedding layout and can't be applied here.";
    }
    this.classes = Object.keys(classes);
    const switched = features !== this.features;
    this.features = features;
    Object.assign(this.colors, colors);
    this.classes.forEach((c, i) => { this.colors[c] ??= PALETTE[i % PALETTE.length]; });
    this.banked = { ...classes };
    this.refit(true);
    const n = Object.values(classes).reduce((a, v) => a + v.length, 0);
    const mode = features === "delta" ? "Transition" : "End state";
    const note = `Loaded ${this.classes.length} classes (${n} examples).${switched ? ` Switched to ${mode}, which is how these were labelled.` : ""}`;
    this.status = `${note} ${this.status}`;
    return note;
  }
}
