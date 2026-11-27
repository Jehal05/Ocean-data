// Small helpers to build HTML and SVG. Every string from the API goes in as text (textContent / attribute), never as markup.
const NS = "http://www.w3.org/2000/svg";

export function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  fill(e, attrs, kids);
  return e;
}

export function s(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  fill(e, attrs, kids);
  return e;
}

function fill(e, attrs, kids) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of kids.flat()) if (c !== undefined && c !== null && c !== false) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
}

export const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

// A marker whose SHAPE says the flag, so colour is never the only signal.
export function marker(shape, x, y, cls = "") {
  const r = 4;
  const c = `mk ${cls}`.trim();
  switch (shape) {
    case "square": return s("rect", { class: c, x: x - r, y: y - r, width: 2 * r, height: 2 * r });
    case "diamond": return s("polygon", { class: c, points: `${x},${y - r - 1} ${x + r + 1},${y} ${x},${y + r + 1} ${x - r - 1},${y}` });
    case "triangle": return s("polygon", { class: c, points: `${x},${y - r - 1} ${x + r + 1},${y + r} ${x - r - 1},${y + r}` });
    case "cross": return s("path", { class: `${c} cross`, d: `M${x - r},${y - r}L${x + r},${y + r}M${x - r},${y + r}L${x + r},${y - r}` });
    case "ring": return s("circle", { class: `${c} ring`, cx: x, cy: y, r });
    default: return s("circle", { class: c, cx: x, cy: y, r: r - 1 });
  }
}

export const path = (points) => points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");

// Inline icons (24x24, stroke only). Decorative: the label next to them says the same thing.
const ICONS = {
  map: ["M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z", "M9 4v14", "M15 6v14"],
  profile: ["M6 3v18", "M6 6h8", "M6 12h12", "M6 18h6"],
  series: ["M3 18l5-6 4 3 4-8 5 5", "M3 21h18"],
  sst: ["M4 4h16v16H4z", "M4 10h16", "M4 15h16", "M10 4v16", "M15 4v16"],
  explore: ["M5 4h10l4 4v12H5z", "M15 4v4h4", "M9 13h6", "M9 17h6"],
  sun: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M12 2v2", "M12 20v2", "M2 12h2", "M20 12h2", "M5 5l1.5 1.5", "M17.5 17.5L19 19", "M5 19l1.5-1.5", "M17.5 6.5L19 5"],
  moon: ["M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"],
  auto: ["M3 5h18v11H3z", "M8 20h8", "M12 16v4"],
};
export function icon(name) {
  return s("svg", { class: "ico", viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" }, (ICONS[name] || []).map((d) => s("path", { d })));
}
