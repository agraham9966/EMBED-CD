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
  add: '<path d="M12 5v14M5 12h14"/>',
  delete: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  rename: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  inspect: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4.5-4.5"/>',
  options: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  classset: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  lens: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  label: '<path d="M4 4l7 17 2.5-7.5L21 11z"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  discard: '<path d="M6 6l12 12M18 6L6 18"/>',
  prev: '<path d="M15 18l-6-6 6-6"/>',
  next: '<path d="M9 18l6-6-6-6"/>',
  more: '<path d="M6 9l6 6 6-6"/>',
};

/** Put an icon in front of a button's text (its textContent is unchanged). A button with no text
 *  is named for screen readers by `label`. */
export function withIcon<T extends HTMLElement>(b: T, name: keyof typeof ICONS, label?: string): T {
  if (label) b.setAttribute("aria-label", label);
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "ecd-icon");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name];
  b.prepend(s);
  return b;
}
