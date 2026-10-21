// Tiny DOM helpers: the panel is plain DOM, so it never shares GeoLibre's UI framework.
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

export function row(...children: (Node | string)[]): HTMLElement {
  const r = el("div", "ecd-row");
  r.append(...children);
  return r;
}

export function select(options: [value: string, label: string][], value?: string): HTMLSelectElement {
  const s = el("select", "ecd-select");
  for (const [v, l] of options) s.append(new Option(l, v));
  if (value !== undefined) s.value = value;
  return s;
}
