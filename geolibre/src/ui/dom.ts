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

// Line icons in the button's text colour (24 x 24, Lucide-style strokes), so they follow the theme.
const ICONS = {
  inspect: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4.5-4.5"/><path d="M11 8v3l2 2"/>',
  lens: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  label: '<path d="M3 3l7 18 2.5-7.5L20 11z"/>',
};

/** Put an icon in front of a button's text (its textContent is unchanged). */
export function withIcon<T extends HTMLElement>(b: T, name: keyof typeof ICONS): T {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "ecd-icon");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name];
  b.prepend(s);
  return b;
}
