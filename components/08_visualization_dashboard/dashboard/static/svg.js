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
