// The QGIS dock's step accordion (a QToolBox): one step open at a time, a caret in each header,
// headers that carry the step's answer, steps that cannot be used yet greyed out, and moving
// forward only once so going back on purpose is respected (dock.py _fold_once / _step_summary).
import { el } from "./dom";

export class Accordion {
  readonly root = el("div", "ecd-accordion");
  private heads: HTMLButtonElement[] = [];
  private bodies: HTMLElement[] = [];
  private titles: string[] = [];
  private current = 0;
  private folded = new Set<string>();

  add(body: HTMLElement): void {
    const i = this.bodies.length;
    const head = el("button", "ecd-acc-head") as HTMLButtonElement;
    head.onclick = () => this.open(i);
    const wrap = el("div", "ecd-acc-body");
    wrap.append(body);
    this.heads.push(head);
    this.bodies.push(wrap);
    this.titles.push("");
    this.root.append(head, wrap);
    this.draw();
  }

  open(i: number): void {
    if (this.heads[i]?.disabled) return;
    this.current = i;
    this.draw();
  }

  setTitles(titles: string[]): void {
    this.titles = titles;
    this.draw();
  }

  setEnabled(enabled: boolean[]): void {
    enabled.forEach((on, i) => { if (this.heads[i]) this.heads[i].disabled = !on; });
    if (this.heads[this.current]?.disabled) this.current = 0;
    this.draw();
  }

  /** Move to step `i`, but only the first time `key` asks. */
  foldOnce(key: string, i: number): void {
    if (this.folded.has(key)) return;
    this.folded.add(key);
    this.open(i);
  }

  resetFold(key: string): void {
    this.folded.delete(key);
  }

  private draw(): void {
    this.heads.forEach((h, i) => {
      h.textContent = `${i === this.current ? "▾" : "▸"} ${this.titles[i] ?? ""}`;
      h.classList.toggle("ecd-acc-open", i === this.current);
      this.bodies[i].hidden = i !== this.current;
    });
  }
}
