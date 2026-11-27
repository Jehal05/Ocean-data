// The five views. They show what the API returns: no smoothing, no filling. A missing value is a gap, never a zero.
import { download, get } from "./api.js";
import {
  bandRuns, baseVariables, boxParam, brushRange, captionLine, cellReadout, citation, colour, columnsOf, extent, flagInfo, fmt, gapSpans, isNum, isoDay, linear, lonSpans,
  monthRange, months, nearestIndex, nextIndex, niceAxis, notices, oneProfile, openLinks, parseBox, plusSeconds, pointReadout, profileList, runs, shortSha, timeAxis, utc,
} from "./logic.js";
import { clear, h, marker, path, s } from "./svg.js";

const W = 760, H = 380, M = { l: 64, r: 20, t: 16, b: 48 };

// ---- small building blocks ---------------------------------------------------------------------------------------
const field = (label, input) => h("label", { class: "field" }, h("span", {}, label), input);
const input = (id, value = "", attrs = {}) => h("input", { id, name: id, value, autocomplete: "off", ...attrs });
const select = (id, options, value) => {
  const e = h("select", { id, name: id }, options.map(([v, t]) => h("option", { value: v, ...(v === value ? { selected: true } : {}) }, t)));
  return e;
};
const val = (id) => document.getElementById(id)?.value ?? "";

function messages(root) {
  const box = h("div", { class: "messages" });
  root.append(box);
  return {
    clear: () => clear(box),
    error: (err) => {
      if (err && err.status === 401) { document.dispatchEvent(new CustomEvent("scats-unauthorised")); return; }     // the session is gone: back to the login page
      clear(box); box.append(h("p", { class: "error", role: "alert" }, err.message || String(err)));
    },
    notes: (list) => list.forEach((t) => box.append(h("p", { class: "notice", role: "status" }, t))),
  };
}

// A flag chip: the same SHAPE as the chart marker, plus the words. Colour is never the only signal.
function chip(flag) {
  const f = flagInfo(flag);
  const text = f.code === null || f.label.startsWith("unknown") ? f.label : `${f.code} ${f.label}`;
  return h("span", { class: "chip", "data-flag": f.code === null ? "none" : String(f.code) },
    s("svg", { viewBox: "0 0 12 12", "aria-hidden": "true", focusable: "false" }, marker(f.shape, 6, 6, `flag${f.code ?? "none"}`)), text);
}

// Copying needs no permission prompt: the clipboard is tried first; if it refuses, the text is shown selected in a visible field.
async function copyText(text, strip, say) {
  strip.querySelector(".copyline")?.remove();
  try { await navigator.clipboard.writeText(text); say("Copied"); return; } catch { /* fall through to the visible field */ }
  const box = h("input", { class: "copyline", readonly: true, value: text, "aria-label": "Text to copy" });
  strip.append(box);
  box.focus();
  box.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  say(ok ? "Copied" : "The text is selected below: press Ctrl+C to copy it");
}

// The strip under each figure, like a caption: which data, which version, which checksum, which flags.
const flagsFor = (quality) => (quality === "all" ? [0, 1, 2, 3, 4, 9] : [1, 2]);          // a dataset's flags_used is a sentence: the quality chosen says which flags these are

function provenance(info0, title, key, synthetic, quality = null) {
  const info = Array.isArray(info0.flags_used) || quality === null ? info0 : { ...info0, flags_used: flagsFor(quality) };
  const c = citation(info);
  const note = h("span", { class: "prov-copied", role: "status" });
  const say = (m) => { note.textContent = m; };
  const strip = h("div", { class: "provenance", "aria-label": "Where this figure comes from" });
  const item = (label, ...kids) => h("span", { class: "prov-item" }, h("span", { class: "prov-label" }, label), ...kids);
  const copyShaBtn = h("button", { type: "button", class: "copy-sha", "aria-label": "Copy the QC checksum" }, "Copy");
  copyShaBtn.addEventListener("click", () => copyText(c.qc_config_sha256, strip, say));
  const copyCaptionBtn = h("button", { type: "button", class: "copy-caption" }, "Copy figure caption");
  copyCaptionBtn.addEventListener("click", () => copyText(captionLine(title, key, info, synthetic), strip, say));
  const flags = Array.isArray(info.flags_used) ? info.flags_used : [];
  strip.append(
    item("data_version", h("span", { class: "prov-value", "data-field": "data_version" }, c.data_version)),
    item("product_version", h("span", { class: "prov-value", "data-field": "product_version" }, c.product_version)),
    item("qc_config_sha256", h("span", { class: "prov-value", "data-field": "qc_config_sha256", title: c.qc_config_sha256 }, shortSha(info.qc_config_sha256)), copyShaBtn),
    item("flags used", h("span", { class: "chips", "data-field": "flags_used" }, flags.length ? flags.map(chip) : h("span", { class: "prov-value" }, "none"))),
    copyCaptionBtn, note);
  return strip;
}

// Placeholders while a request is open; the answer replaces them (or an error does).
const skeleton = () => h("div", { class: "skeleton-box", "aria-busy": "true" }, h("span", { class: "sr", role: "status" }, "Loading"), h("div", { class: "skeleton big" }), h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
const busy = (out) => { clear(out); out.append(skeleton()); };
const empty = (text, step, onClick) => {
  const e = h("div", { class: "empty" }, h("p", {}, text), h("p", { class: "hint" }, step));
  if (onClick) { const b = h("button", { type: "button" }, "Clear the filters"); b.addEventListener("click", onClick); e.append(b); }
  return e;
};

// Filters sit in a collapsible left panel; the result (chart, then table) is on the right.
function layout(root, title, filters, result) {
  const panel = h("details", { class: "panel-filters", open: !window.matchMedia("(max-width: 760px)").matches }, h("summary", {}, title), filters);     // on a phone the chart comes first; the filters are one tap away
  root.append(h("div", { class: "viewgrid" }, panel, result));
}

// Pointer position in the units of the svg's viewBox.
const svgPoint = (svg, ev, w, ht) => { const r = svg.getBoundingClientRect(); return [(ev.clientX - r.left) * (w / r.width), (ev.clientY - r.top) * (ht / r.height)]; };

// A chart is always paired with its numbers: a button toggles a table.
function tableBlock(caption, columns, rows, units = {}) {
  const wrap = h("div", { class: "tableblock" });
  const tbl = h("table", { hidden: true, id: `t${Math.random().toString(36).slice(2, 8)}` },
    h("caption", {}, caption),
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col" }, units[c] ? `${c} (${units[c]})` : c)))),
    h("tbody", {}, rows.map((r, i) => h("tr", { "data-row": i }, columns.map((c) => h("td", {}, c.endsWith("_qc") && r[c] !== null && r[c] !== undefined ? chip(r[c]) : isNum(r[c]) ? fmt(r[c], 4) : r[c] === null || r[c] === undefined ? "no value" : String(r[c])))))));
  const btn = h("button", { type: "button", "aria-expanded": "false", "aria-controls": tbl.id }, "Show the numbers as a table");
  btn.addEventListener("click", () => {
    tbl.hidden = !tbl.hidden;
    btn.setAttribute("aria-expanded", String(!tbl.hidden));
    btn.textContent = tbl.hidden ? "Show the numbers as a table" : "Hide the table";
  });
  wrap.append(btn, tbl);
  return wrap;
}

function frame(title, desc) {
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": title, preserveAspectRatio: "xMidYMid meet" });
  svg.append(s("title", {}, title), s("desc", {}, desc));
  return svg;
}

function axes(svg, xs, ys, labels) {
  const g = s("g", { class: "axes" });
  for (const t of xs.ticks) {
    const x = xs.scale(t);
    g.append(s("line", { class: "grid", x1: x, x2: x, y1: M.t, y2: H - M.b }), s("text", { class: "tick", x, y: H - M.b + 16, "text-anchor": "middle" }, xs.format ? xs.format(t) : fmt(t)));
  }
  for (const t of ys.ticks) {
    const y = ys.scale(t);
    g.append(s("line", { class: "grid", x1: M.l, x2: W - M.r, y1: y, y2: y }), s("text", { class: "tick", x: M.l - 6, y: y + 4, "text-anchor": "end" }, ys.format ? ys.format(t) : fmt(t)));
  }
  g.append(s("rect", { class: "plot", x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "none" }));
  g.append(s("text", { class: "label", x: (M.l + W - M.r) / 2, y: H - 8, "text-anchor": "middle" }, labels.x));
  g.append(s("text", { class: "label", x: 14, y: (M.t + H - M.b) / 2, "text-anchor": "middle", transform: `rotate(-90 14 ${(M.t + H - M.b) / 2})` }, labels.y));
  svg.append(g);
}

const timeTick = (span) => (ms) => new Date(ms).toISOString().slice(0, span > 4 * 86400000 ? 10 : 16).replace("T", " ");

async function datasetsOf(ctx, params) {
  const r = await get("/datasets", { page_size: ctx.pageSize, ...params });
  return r;
}

// ---- 1. map ------------------------------------------------------------------------------------------------------
const degLabel = (v, pos, neg) => (v === 0 ? "0°" : `${Math.abs(v)}°${v < 0 ? neg : pos}`);

export async function mapView(root, ctx) {
  const msg = messages(root);
  const form = h("form", { class: "filters", novalidate: true },
    field("Search text", input("q")), field("Platform type", input("platform_type", "", { placeholder: "e.g. argo_float" })),
    field("Variable", input("variable", "", { placeholder: "e.g. temperature" })),
    field("Box west,south,east,north", input("bbox", "", { placeholder: "60,5,80,25" })),
    field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
    h("button", { type: "submit" }, "Apply filters"));
  const out = h("div", { class: "result" });
  root.append(h("p", {}, "Footprints of published datasets on a drawn graticule. No coastline or tiles are drawn (offline demo)."));
  layout(root, "Filters", form, out);
  let seq = 0;                                           // only the newest request may draw: an older answer arriving late is dropped
  async function load() {
    const mine = ++seq;
    msg.clear();
    busy(out);
    const box = val("bbox").trim() ? parseBox(val("bbox")) : null;
    if (box && box.error) { clear(out); return msg.error(new Error(box.error)); }
    try {
      const r = await datasetsOf(ctx, { q: val("q"), platform_type: val("platform_type"), variable: val("variable"), bbox: box ? boxParam(box) : "", time_from: val("time_from"), time_to: val("time_to") });
      if (mine !== seq) return;
      clear(out);
      msg.notes(notices(r));
      drawMap(out, r.items, box, ctx, () => { form.reset(); load(); });
    } catch (e) { if (mine === seq) { clear(out); msg.error(e); } }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); load(); });
  await load();
}

// One card per footprint: shown on hover or focus, pinned with Enter or a click, closed with Escape.
function mapCard(wrap) {
  const card = h("div", { class: "card", role: "region", "aria-label": "Dataset details", hidden: true });
  wrap.append(card);
  let pinned = null, from = null;
  const fill = (d) => {
    clear(card);
    const row = (k, v) => [h("dt", {}, k), h("dd", {}, v)];
    card.append(h("h3", {}, d.title || d.dataset_key),
      h("dl", {}, row("Dataset", d.dataset_key), row("Platform", `${d.platform_type} ${d.platform_id ?? ""}`.trim()), row("Time", `${utc(d.time_start)} to ${utc(d.time_end)}`),
        row("Variables", (d.variables || []).map((v) => v.name || v).join(", ") || "none listed")),
      h("p", { class: "buttons" }, openLinks(d).map((l) => h("a", { href: l.href }, l.label))));
  };
  wrap.addEventListener("keydown", (e) => { if (e.key === "Escape" && !card.hidden) { e.preventDefault(); close(); } });
  function close() {
    wrap.querySelectorAll(".fp.pinned").forEach((g) => g.classList.remove("pinned"));
    pinned = null; card.hidden = true; card.classList.remove("pinned");
    const back = from; from = null;
    if (back) { back.focus(); card.hidden = true; }                                // focusing the footprint would open its card again
  }
  return {
    show(d, g) {
      if (pinned) return;
      fill(d);
      const a = g.getBoundingClientRect(), b = wrap.getBoundingClientRect();
      card.classList.toggle("left", a.left + a.width / 2 > b.left + b.width / 2);          // the card goes on the side away from the footprint
      card.hidden = false;
    },
    hide() { if (!pinned) card.hidden = true; },
    pin(d, g) { pinned = d; from = g; fill(d); card.classList.add("pinned"); card.hidden = false; wrap.querySelectorAll(".fp.pinned").forEach((x) => x.classList.remove("pinned")); g.classList.add("pinned"); card.querySelector("a")?.focus(); },
  };
}

function mapLegend() {
  const kinds = [["profile", "profile (solid)"], ["timeseries", "time series (hatched)"], ["grid", "grid (dotted)"]];
  return h("div", { class: "legend", "aria-label": "Dataset types" }, h("span", { class: "hint" }, "Dataset type:"), kinds.map(([k, text]) =>
    h("span", { class: "chip" }, s("svg", { class: `swatch type-${k}`, viewBox: "0 0 28 16", "aria-hidden": "true", focusable: "false" }, s("rect", { x: 1, y: 1, width: 26, height: 14 })), text)));
}

function drawMap(out, items, box, ctx, clearFilters) {
  ctx.setSynthetic(items.length > 0 && items.every((d) => d.is_synthetic));
  out.append(h("p", { class: "count", id: "map-count" }, `${items.length} dataset${items.length === 1 ? "" : "s"} shown`));
  const withBox = items.filter((d) => [d.lat_min, d.lat_max, d.lon_min, d.lon_max].every(isNum));
  if (!items.length) return out.append(empty("No datasets match these filters.", "Widen the time range, remove the box, or clear the search text.", clearFilters));
  if (!withBox.length) return;
  const pieces = withBox.flatMap((d) => lonSpans(d.lon_min, d.lon_max).map(([a, b]) => ({ d, a, b })));
  const lonE = extent(pieces.flatMap((p) => [p.a, p.b]).concat(box ? [box.west, box.east] : []));
  const latE = extent(withBox.flatMap((d) => [d.lat_min, d.lat_max]).concat(box ? [box.south, box.north] : []));
  const pad = (e, m) => [e[0] - Math.max(m, (e[1] - e[0]) * 0.1), e[1] + Math.max(m, (e[1] - e[0]) * 0.1)];
  const lonD = pad(lonE, 3), latD = pad(latE, 3);
  const xa = niceAxis(lonD[0], lonD[1], 6), ya = niceAxis(latD[0], latD[1], 5);
  const x = linear(xa.min, xa.max, M.l, W - M.r), y = linear(ya.min, ya.max, H - M.b, M.t);
  const svg = frame("Map of dataset footprints", `${items.length} datasets drawn as rectangles or points on a longitude and latitude graticule`);
  svg.append(s("defs", {},
    s("linearGradient", { id: "ocean", x1: 0, y1: 0, x2: 0, y2: 1 }, s("stop", { class: "ocean-a", offset: "0" }), s("stop", { class: "ocean-b", offset: "1" })),
    s("pattern", { id: "fphatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, s("line", { class: "fpline", x1: 0, y1: 0, x2: 0, y2: 6 })),
    s("pattern", { id: "fpdots", width: 6, height: 6, patternUnits: "userSpaceOnUse" }, s("circle", { class: "fpdot", cx: 3, cy: 3, r: 1.2 }))));
  svg.append(s("rect", { class: "mapbg", x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "url(#ocean)" }));
  axes(svg, { ticks: xa.ticks, scale: x, format: (t) => `${fmt(t, 4)}°` }, { ticks: ya.ticks, scale: y, format: (t) => `${fmt(t, 4)}°` }, { x: "longitude", y: "latitude" });
  const grat = s("g", { class: "graticule" });                                   // lines and labels every 30 degrees that fall inside the drawn area
  for (let v = Math.ceil(xa.min / 30) * 30; v <= xa.max; v += 30) grat.append(s("line", { class: "grid major", x1: x(v), x2: x(v), y1: M.t, y2: H - M.b }), s("text", { class: "tick glabel", x: x(v) + 3, y: M.t + 11 }, degLabel(v, "E", "W")));
  for (let v = Math.ceil(ya.min / 30) * 30; v <= ya.max; v += 30) grat.append(s("line", { class: "grid major", x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }), s("text", { class: "tick glabel", x: W - M.r - 3, y: y(v) - 3, "text-anchor": "end" }, degLabel(v, "N", "S")));
  svg.append(grat);
  if (box) {
    const spans = box.wraps ? [[box.west, 180], [-180, box.east]] : [[box.west, box.east]];
    for (const [a, b] of spans) svg.append(s("rect", { class: "bbox", x: x(a), y: y(box.north), width: Math.max(1, x(b) - x(a)), height: Math.max(1, y(box.south) - y(box.north)) }));
  }
  const wrap = h("div", { class: "mapwrap" });
  const card = mapCard(wrap);
  for (const p of pieces) {
    const d = p.d, w = x(p.b) - x(p.a), ht = y(d.lat_min) - y(d.lat_max);
    const cx = (x(p.a) + x(p.b)) / 2, cy = (y(d.lat_min) + y(d.lat_max)) / 2;
    const g = s("g", { class: `fp type-${String(d.dataset_type).replace(/[^a-z0-9_]/gi, "")}`, tabindex: 0, role: "img", "aria-label": `${d.dataset_key}: ${d.platform_type}` }, s("title", {}, `${d.dataset_key} (${d.platform_type})`));
    if (w < 3 && ht < 3) g.append(marker("diamond", cx, cy, "fpmark"));
    else g.append(s("rect", { class: "fprect", x: x(p.a) - (w < 3 ? 3 : 0), y: y(d.lat_max) - (ht < 3 ? 3 : 0), width: Math.max(6, w), height: Math.max(6, ht) }));
    for (const ev of ["pointerenter", "focus"]) g.addEventListener(ev, () => card.show(d, g));
    for (const ev of ["pointerleave", "blur"]) g.addEventListener(ev, () => card.hide());
    g.addEventListener("click", () => card.pin(d, g));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.pin(d, g); } });
    svg.append(g);
  }
  wrap.prepend(svg);
  out.append(mapLegend(), wrap);
  const cols = ["dataset_key", "title", "dataset_type", "platform_type", "platform_id", "time_start", "time_end", "lat_min", "lat_max", "lon_min", "lon_max"];
  out.append(h("table", { class: "list" }, h("caption", {}, "Datasets"), h("thead", {}, h("tr", {}, cols.map((c) => h("th", { scope: "col" }, c)), h("th", { scope: "col" }, "open in"))),
    h("tbody", {}, items.map((d) => h("tr", {}, cols.map((c) => h("td", {}, isNum(d[c]) ? fmt(d[c], 4) : c.startsWith("time_") ? utc(d[c]) : d[c] === null ? "no value" : String(d[c]))),
      h("td", {}, openLinks(d).flatMap((l) => [h("a", { href: l.href }, l.label), " "])))))));
}

// ---- 2. profile --------------------------------------------------------------------------------------------------
export async function profileView(root, ctx, params) {
  const msg = messages(root);
  let list;
  try { list = (await datasetsOf(ctx, { dataset_type: "profile" })).items; } catch (e) { return msg.error(e); }
  if (!list.length) return root.append(empty("There are no published profile datasets.", "Ask an administrator to publish one, or open the Map to see what is available."));
  const form = h("form", { class: "filters", novalidate: true },
    field("Dataset", select("dataset", list.map((d) => [d.dataset_key, d.dataset_key]), params.get("key") || list[0].dataset_key)),
    field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
    field("Platform", input("platform")), h("button", { type: "submit" }, "Find profiles"));
  const pick = h("div", { class: "filters pick" });
  const out = h("div", { class: "result" });
  layout(root, "Filters", h("div", {}, form, pick), out);
  let profiles = [];
  let findSeq = 0, showSeq = 0;
  async function find() {
    const mine = ++findSeq;
    showSeq++;
    msg.clear(); clear(pick); busy(out);
    const key = val("dataset");
    ctx.setSynthetic(list.find((d) => d.dataset_key === key)?.is_synthetic === true);
    try {
      const r = await get(`/datasets/${encodeURIComponent(key)}/subset`, { depth_max: 6, page_size: ctx.pageSize, time_from: val("time_from"), time_to: val("time_to"), platform: val("platform") });
      if (mine !== findSeq) return;
      msg.notes(notices(r));
      profiles = profileList(r.rows);
      if (!profiles.length) { clear(out); out.append(empty("No profiles match these filters.", "Widen the dates or clear the platform, then press Find profiles.")); return; }
      const sel = select("profile", profiles.map((p, i) => [String(i), `${p.platform_id} #${p.source_record}  ${p.time}  (${fmt(p.latitude, 4)}, ${fmt(p.longitude, 4)})`]), "0");
      const q = select("quality", [["good", "good values only (flags 1, 2)"], ["all", "all values with their flags"]], "good");
      sel.addEventListener("change", show);
      q.addEventListener("change", show);
      pick.append(h("p", { class: "count" }, `${profiles.length} profile${profiles.length === 1 ? "" : "s"} listed`), field("Profile", sel), field("Quality", q));
    } catch (e) { if (mine === findSeq) { clear(out); msg.error(e); } return; }
    if (mine === findSeq) await show();
  }
  async function show() {
    const mine = ++showSeq;
    msg.clear(); busy(out);
    const p = profiles[Number(val("profile"))];
    const key = val("dataset");
    try {
      const r = await get(`/datasets/${encodeURIComponent(key)}/subset`, { platform: p.platform_id, time_from: p.time, time_to: plusSeconds(p.time, 1), quality: val("quality"), page_size: ctx.pageSize });
      if (mine !== showSeq) return;
      clear(out);
      msg.notes(notices(r));
      const rows = oneProfile(r.rows, p);
      out.append(h("h3", {}, `${p.platform_id} #${p.source_record}, ${p.time}`));
      const vars = r.columns.filter((c) => !c.endsWith("_qc") && !["platform_id", "source_record", "time", "latitude", "longitude", "level", "depth"].includes(c));
      const readout = h("div", { class: "readout", role: "status", id: "readout" }, "Point at a value, or focus a chart and use the arrow keys, to read it.");
      const table = tableBlock("Profile values", ["depth", ...vars.flatMap((v) => [v, `${v}_qc`])], rows, r.units);
      const panels = vars.map((v) => profilePanel(v, rows, r.units[v], (variable, i) => {
        panels.forEach((pn) => pn.set(i));
        table.querySelectorAll("tr.hl").forEach((tr) => tr.classList.remove("hl"));
        clear(readout);
        if (i === null) { readout.append("Point at a value, or focus a chart and use the arrow keys, to read it."); return; }
        table.querySelector(`tr[data-row="${i}"]`)?.classList.add("hl");
        const f = rows[i][`${variable}_qc`];
        readout.append(`${pointReadout(variable, rows[i][variable], r.units[variable], rows[i].depth, f)} `, f === null || f === undefined ? null : chip(f));
      }));
      const row = h("div", { class: "panels" }, panels.map((pn) => pn.svg));
      out.append(row, readout, provenance(r, "Profile", key, ctx.isSynthetic(key), val("quality")), legend(), table);
    } catch (e) { if (mine === showSeq) { clear(out); msg.error(e); } }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); find(); });
  await find();
}

// One panel. `report(variable, index)` is told which row the pointer or the arrow keys are on (null: none); `set(index)` draws the crosshair for a row.
function profilePanel(variable, rows, unit, report) {
  const xs = rows.map((r) => r[variable]), ds = rows.map((r) => r.depth);
  const w = 360, ht = 340, m = { l: 56, r: 14, t: 14, b: 44 };
  const svg = s("svg", { viewBox: `0 0 ${w} ${ht}`, class: "chart panel", role: "img", "aria-label": `${variable} against depth` }, s("title", {}, `${variable} against depth`));
  const xe = extent(xs), de = extent(ds);
  if (!xe || !de) { svg.append(s("text", { class: "label", x: w / 2, y: ht / 2, "text-anchor": "middle" }, `${variable}: no values`)); return { svg, set() {} }; }
  const xa = niceAxis(xe[0], xe[1], 4), da = niceAxis(0, de[1], 5);
  const x = linear(xa.min, xa.max, m.l, w - m.r), y = linear(da.min, da.max, m.t, ht - m.b);        // depth grows downwards
  for (const t of xa.ticks) svg.append(s("line", { class: "grid", x1: x(t), x2: x(t), y1: m.t, y2: ht - m.b }), s("text", { class: "tick", x: x(t), y: ht - m.b + 15, "text-anchor": "middle" }, fmt(t)));
  for (const t of da.ticks) svg.append(s("line", { class: "grid", x1: m.l, x2: w - m.r, y1: y(t), y2: y(t) }), s("text", { class: "tick", x: m.l - 5, y: y(t) + 4, "text-anchor": "end" }, fmt(t)));
  svg.append(s("rect", { class: "plot", x: m.l, y: m.t, width: w - m.l - m.r, height: ht - m.t - m.b, fill: "none" }),
    s("text", { class: "label", x: (m.l + w - m.r) / 2, y: ht - 6, "text-anchor": "middle" }, `${variable}${unit ? ` (${unit})` : ""}`),
    s("text", { class: "label", x: 12, y: (m.t + ht - m.b) / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${(m.t + ht - m.b) / 2})` }, "depth (m)"));
  for (const run of runs(xs, ds)) svg.append(s("path", { class: "line", d: path(run.map(([a, b]) => [x(a), y(b)])) }));
  const marks = [];
  rows.forEach((r, i) => {
    const f = flagInfo(r[`${variable}_qc`]);
    if (isNum(r[variable]) && isNum(r.depth)) { const mk = marker(f.shape, x(r[variable]), y(r.depth), `flag${f.code ?? "none"}`); marks[i] = mk; svg.append(mk); }
  });
  const hLine = s("line", { class: "crosshair", x1: m.l, x2: w - m.r, hidden: true }), vLine = s("line", { class: "crosshair", y1: m.t, y2: ht - m.b, hidden: true });
  const hit = s("rect", { class: "hit", x: m.l, y: m.t, width: w - m.l - m.r, height: ht - m.t - m.b });
  svg.append(hLine, vLine, hit);
  const py = rows.map((r, i) => (marks[i] && isNum(r.depth) ? y(r.depth) : NaN));
  const valid = py.map((v, i) => (Number.isNaN(v) ? -1 : i)).filter((i) => i >= 0);
  let cur = null;
  function set(i) {
    marks.forEach((mk) => mk?.classList.remove("on"));
    const on = i !== null && isNum(rows[i]?.depth);
    hLine.toggleAttribute("hidden", !on);
    vLine.toggleAttribute("hidden", !(on && isNum(rows[i][variable])));
    if (!on) return;
    marks[i]?.classList.add("on");
    for (const [a, b] of [["y1", y(rows[i].depth)], ["y2", y(rows[i].depth)]]) hLine.setAttribute(a, b);
    if (isNum(rows[i][variable])) { vLine.setAttribute("x1", x(rows[i][variable])); vLine.setAttribute("x2", x(rows[i][variable])); }
  }
  const go = (i) => { cur = i; report(variable, i); };
  hit.addEventListener("pointermove", (ev) => { const [, yy] = svgPoint(svg, ev, w, ht); const i = nearestIndex(py, yy); if (i >= 0 && i !== cur) go(i); });
  if (valid.length) {
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", `${variable} against depth. Use the up and down arrow keys to move from value to value; Escape clears.`);
    svg.addEventListener("keydown", (e) => {
      const at = valid.indexOf(cur);
      if (e.key === "ArrowDown") go(valid[Math.min(valid.length - 1, at + 1)]);
      else if (e.key === "ArrowUp") go(valid[at <= 0 ? 0 : at - 1]);
      else if (e.key === "Home") go(valid[0]);
      else if (e.key === "End") go(valid[valid.length - 1]);
      else if (e.key === "Escape") go(null);
      else return;
      e.preventDefault();
    });
  }
  return { svg, set };
}

function legend() {
  return h("div", { class: "legend", "aria-label": "Flag marker shapes" }, h("span", { class: "hint" }, "Flags:"), [0, 1, 2, 3, 4, 9].map(chip));
}

// ---- 3. series ---------------------------------------------------------------------------------------------------
export async function seriesView(root, ctx, params) {
  const msg = messages(root);
  let list;
  try { list = (await get("/products", { page_size: ctx.pageSize, include_stale: "true" })).items.filter((p) => p.kind === "series_hourly" || p.kind === "series_daily"); } catch (e) { return msg.error(e); }
  if (!list.length) return root.append(empty("There are no series products built yet.", "Build one with the products step, then come back."));
  const form = h("form", { class: "filters", novalidate: true },
    field("Product", select("product", list.map((p) => [p.product_key, `${p.product_key}${p.stale ? " (stale)" : ""}`]), params.get("key") || list[0].product_key)),
    field("Variable", select("variable", [], "")), field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
    field("Platform", input("platform")), h("label", { class: "check" }, h("input", { type: "checkbox", id: "allow_stale" }), " read a stale product anyway"),
    h("button", { type: "submit" }, "Show series"));
  const out = h("div", { class: "result" });
  layout(root, "Filters", form, out);
  function syncVariables() {
    const p = list.find((x) => x.product_key === val("product"));
    const sel = document.getElementById("variable");
    clear(sel);
    baseVariables(p.variables).forEach((v) => sel.append(h("option", { value: v }, v)));
    if (p.time_start && !val("time_from")) {
      const t0 = new Date(p.time_start), t1 = new Date(Math.min(Date.parse(p.time_end), t0.getTime() + (p.kind === "series_hourly" ? 30 : 180) * 86400000));
      document.getElementById("time_from").value = t0.toISOString().slice(0, 10);
      document.getElementById("time_to").value = t1.toISOString().slice(0, 10);
    }
  }
  document.getElementById("product").addEventListener("change", () => { document.getElementById("time_from").value = ""; document.getElementById("time_to").value = ""; syncVariables(); });
  syncVariables();
  let seq = 0;
  async function show() {
    const mine = ++seq;
    msg.clear(); busy(out);
    const p = list.find((x) => x.product_key === val("product"));
    ctx.setSynthetic(ctx.isSynthetic(p.dataset_key));
    try {
      const q = { variable: columnsOf(p.variables, val("variable")).join(","), time_from: val("time_from"), time_to: val("time_to"), platform: val("platform"), page_size: ctx.pageSize, allow_stale: document.getElementById("allow_stale").checked ? "true" : "" };
      const page = (n) => get(`/products/${encodeURIComponent(p.product_key)}/subset`, { ...q, page: n });
      const r = await page(1);
      if (mine !== seq) return;
      msg.notes(notices(r));
      await drawSeries(out, r, val("variable"), ctx, page, () => mine !== seq, p, show);
    } catch (e) { if (mine === seq) { clear(out); msg.error(e); } }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); show(); });
  await show();
}

let keepFocus = null;                                                               // the control to focus again after the chart is redrawn

async function drawSeries(out, r, v, ctx, page, cancelled, product, show) {
  // the API pages: follow the pages until the rows of the selection are all here, within the user's limits
  let rows = r.rows, resp = r;
  while (resp.truncated && rows.length < ctx.maxSeriesRows) {
    resp = await page(resp.next_page);
    if (cancelled()) return;
    rows = rows.concat(resp.rows);
  }
  clear(out);
  if (resp.truncated) out.append(h("p", { class: "notice", role: "status" }, `Only the first ${rows.length} of ${r.n_rows_selected} rows are drawn. Narrow the time range.`));
  const times = rows.map((x) => Date.parse(x.time));
  const mean = rows.map((x) => x[`${v}_mean`]), lo = rows.map((x) => x[`${v}_min`]), hi = rows.map((x) => x[`${v}_max`]), cnt = rows.map((x) => x[`${v}_count`]);
  const te = extent(times), ve = extent(mean.concat(lo, hi));
  const prov = provenance(r, `${v} over time`, product.product_key, ctx.isSynthetic(product.dataset_key));
  if (!te || !ve) {
    out.append(empty(`No good values of ${v} in this selection.`, "Widen the time range or choose another variable, then press Show series."), prov);
    return;
  }
  const xa = timeAxis(te[0], te[1], 6), ya = niceAxis(ve[0], ve[1], 5);
  const span = te[1] - te[0];
  const svg = frame(`${v} over time`, `${rows.length} bins, mean line with minimum and maximum band`);
  const x = linear(xa.min, xa.max, M.l, W - M.r), y = linear(ya.min, ya.max, H - M.b, M.t);
  svg.append(s("defs", {}, s("pattern", { id: "gaphatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, s("line", { class: "hatch-line", x1: 0, y1: 0, x2: 0, y2: 6 }))));
  axes(svg, { ticks: xa.ticks, scale: x, format: timeTick(span) }, { ticks: ya.ticks, scale: y }, { x: "time (UTC)", y: `${v} (${r.units[`${v}_mean`] || ""})` });
  const clampX = (px) => Math.min(W - M.r, Math.max(M.l, px));
  for (const [a, b] of gapSpans(times, cnt)) {                                      // a bin with no good values: a hatched strip, not a zero
    const x0 = clampX(x(a)), x1 = clampX(x(b));
    svg.append(s("rect", { class: "gap", x: x0, y: M.t, width: Math.max(3, x1 - x0), height: H - M.t - M.b }, s("title", {}, "no good values")));
    if (x1 - x0 > 90) svg.append(s("text", { class: "gaplabel", x: (x0 + x1) / 2, y: M.t + 12, "text-anchor": "middle" }, "no good values"));
  }
  for (const run of bandRuns(times, lo, hi)) svg.append(s("path", { class: "band", d: `${path(run.map(([t, a]) => [x(t), y(a)]))}${path(run.slice().reverse().map(([t, , b]) => [x(t), y(b)])).replace("M", "L")}Z` }));
  for (const run of runs(times, mean)) svg.append(s("path", { class: "line", d: path(run.map(([t, a]) => [x(t), y(a)])) }));
  const brush = s("rect", { class: "brush", y: M.t, height: H - M.t - M.b, hidden: true });
  const hit = s("rect", { class: "hit brushhit", x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b });
  svg.append(brush, hit);
  let down = null;
  const apply = (a, b) => {                                                         // the same API call as the form: set From and To, then ask again
    document.getElementById("time_from").value = isoDay(a);
    document.getElementById("time_to").value = isoDay(b);
    show();
  };
  hit.addEventListener("pointerdown", (ev) => { down = svgPoint(svg, ev, W, H)[0]; hit.setPointerCapture?.(ev.pointerId); brush.setAttribute("x", down); brush.setAttribute("width", 0); brush.removeAttribute("hidden"); });
  hit.addEventListener("pointermove", (ev) => {
    if (down === null) return;
    const px = clampX(svgPoint(svg, ev, W, H)[0]);
    brush.setAttribute("x", Math.min(down, px)); brush.setAttribute("width", Math.abs(px - down));
  });
  hit.addEventListener("pointerup", (ev) => {
    if (down === null) return;
    const [a, b] = brushRange(down, svgPoint(svg, ev, W, H)[0], M.l, W - M.r, xa.min, xa.max);
    const moved = Math.abs(svgPoint(svg, ev, W, H)[0] - down) >= 4;
    down = null; brush.setAttribute("hidden", "");
    if (moved && isoDay(b) >= isoDay(a)) apply(a, b);
  });
  out.append(svg);
  const ce = extent(cnt);
  if (ce) {
    const ca = niceAxis(0, ce[1], 2), cy = linear(ca.min, ca.max, 70, 8);
    const bars = s("svg", { viewBox: `0 0 ${W} 80`, class: "chart counts", role: "img", "aria-label": `Number of observations in each bin of ${v}` }, s("title", {}, `Observations per bin (${v})`));
    for (const t of xa.ticks) bars.append(s("line", { class: "grid", x1: x(t), x2: x(t), y1: 8, y2: 70 }));       // the same x scale as the chart above
    rows.forEach((row, i) => { if (isNum(cnt[i])) bars.append(s("rect", { class: "bar", x: x(times[i]) - 1, y: cy(cnt[i]), width: 2, height: Math.max(0, cy(0) - cy(cnt[i])) })); });
    bars.append(s("text", { class: "tick", x: 8, y: 12 }, `count per bin, 0 to ${fmt(ca.max)}`));
    out.append(bars);
  }
  // the keyboard route to the same thing as the brush: two sliders over the days drawn
  const days = Math.max(1, Math.round((xa.max - xa.min) / 86400000));
  const dayAt = (i) => isoDay(xa.min + i * 86400000);
  const rFrom = h("input", { type: "range", id: "range_from", min: 0, max: days, value: 0, "aria-describedby": "range-help" });
  const rTo = h("input", { type: "range", id: "range_to", min: 0, max: days, value: days, "aria-describedby": "range-help" });
  const tFrom = h("output", { for: "range_from" }, dayAt(0)), tTo = h("output", { for: "range_to" }, dayAt(days));
  const sync = () => {                                                               // moving a slider fills From and To; the button asks again
    tFrom.textContent = dayAt(Number(rFrom.value)); tTo.textContent = dayAt(Number(rTo.value));
    document.getElementById("time_from").value = tFrom.textContent; document.getElementById("time_to").value = tTo.textContent;
  };
  rFrom.addEventListener("input", () => { if (Number(rFrom.value) > Number(rTo.value)) rTo.value = rFrom.value; sync(); });
  rTo.addEventListener("input", () => { if (Number(rTo.value) < Number(rFrom.value)) rFrom.value = rTo.value; sync(); });
  const applyBtn = h("button", { type: "button", id: "range_apply" }, "Apply range");
  applyBtn.addEventListener("click", () => { keepFocus = "range_apply"; apply(xa.min + Number(rFrom.value) * 86400000, xa.min + Number(rTo.value) * 86400000); });
  out.append(h("div", { class: "rangebox" }, h("label", { for: "range_from" }, "Range start"), rFrom, tFrom, h("label", { for: "range_to" }, "Range end"), rTo, tTo, applyBtn),
    h("p", { class: "hint", id: "range-help" }, "Drag across the chart to zoom, or move the two sliders and press Apply range: From and To are set and the same request is made again."),
    prov,
    h("p", { class: "hint" }, "Band: minimum to maximum within each bin. A bin with no good values is a hatched strip marked \"no good values\", and the line has a gap there."),
    tableBlock("Series values", ["time", `${v}_mean`, `${v}_min`, `${v}_max`, `${v}_count`].filter((c) => r.columns.includes(c)), rows, r.units));
  if (keepFocus) { document.getElementById(keepFocus)?.focus(); keepFocus = null; }
}

// ---- 4. SST ------------------------------------------------------------------------------------------------------
const PLAY_MS = 1000;
let playTimer = null;
const stopPlay = () => { if (playTimer !== null) { clearInterval(playTimer); playTimer = null; } };

export async function sstView(root, ctx, params) {
  stopPlay();
  const msg = messages(root);
  let list;
  try { list = (await get("/products", { page_size: ctx.pageSize, kind: "sst_monthly_mean", include_stale: "true" })).items; } catch (e) { return msg.error(e); }
  if (!list.length) return root.append(empty("There is no monthly SST product built yet.", "Build one with the products step, then come back."));
  const p0 = list.find((p) => p.product_key === params.get("key")) || list[0];
  const monthList = months(p0.time_start, p0.time_end);
  const form = h("form", { class: "filters", novalidate: true },
    field("Product", select("product", list.map((p) => [p.product_key, p.product_key]), p0.product_key)),
    field("Month", select("month", monthList.map((m) => [m, m]), "")),
    field("Box west,south,east,north", input("bbox", `${p0.lon_min},${p0.lat_min},${Math.min(p0.lon_max, p0.lon_min + 3)},${Math.min(p0.lat_max, p0.lat_min + 3)}`)),
    h("label", { class: "check" }, h("input", { type: "checkbox", id: "allow_stale" }), " read a stale product anyway"), h("button", { type: "submit" }, "Show month"));
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const slider = h("input", { type: "range", id: "month_slider", min: 0, max: Math.max(0, monthList.length - 1), value: 0 });
  const monthText = h("output", { id: "month_text", for: "month_slider" }, monthList[0] || "");
  const play = h("button", { type: "button", id: "play", disabled: reduce || monthList.length < 2 }, "Play");
  const playNote = h("span", { class: "hint", id: "play-note" }, reduce ? "Play is off because your system asks for less motion. Use the month slider." : "");
  const controls = h("div", { class: "monthbox" }, h("label", { for: "month_slider" }, "Month"), slider, monthText, play, playNote);
  const out = h("div", { class: "result" });
  layout(root, "Filters", form, h("div", {}, controls, out));
  let seq = 0;
  const sel = document.getElementById("month");
  const setMonth = (i) => { sel.selectedIndex = i; slider.value = String(i); monthText.textContent = monthList[i] || ""; };
  async function show() {
    const mine = ++seq;
    msg.clear(); busy(out);
    const p = list.find((x) => x.product_key === val("product"));
    const box = parseBox(val("bbox"));
    const range = monthRange(val("month"));
    if (box.error) { clear(out); return msg.error(new Error(box.error)); }
    if (!range) { clear(out); return msg.error(new Error("choose a month")); }
    ctx.setSynthetic(ctx.isSynthetic(p.dataset_key));
    try {
      const q = { bbox: boxParam(box), time_from: range[0], time_to: range[1], page_size: ctx.pageSize, allow_stale: document.getElementById("allow_stale").checked ? "true" : "" };
      const page = (n) => get(`/products/${encodeURIComponent(p.product_key)}/subset`, { ...q, page: n });
      const r = await page(1);
      if (mine !== seq) return;
      msg.notes(notices(r));
      let rows = r.rows, resp = r;
      while (resp.truncated && rows.length < ctx.maxSeriesRows) {
        resp = await page(resp.next_page);
        if (mine !== seq) return;
        rows = rows.concat(resp.rows);
      }
      clear(out);
      if (resp.truncated) out.append(h("p", { class: "notice", role: "status" }, `Only the first ${rows.length} of ${r.n_rows_selected} cells are drawn. Use a smaller box.`));
      drawGrid(out, rows, r, p.variables[0], prov(r, p, val("month")));
    } catch (e) { if (mine === seq) { clear(out); msg.error(e); } }
  }
  const prov = (r, p, month) => provenance(r, `Monthly mean sea surface temperature, ${month}`, p.product_key, ctx.isSynthetic(p.dataset_key));
  document.getElementById("product").addEventListener("change", () => navigate("sst", val("product")));
  sel.addEventListener("change", () => setMonth(sel.selectedIndex));
  slider.addEventListener("input", () => setMonth(Number(slider.value)));
  slider.addEventListener("change", show);
  play.addEventListener("click", () => {
    if (playTimer !== null) { stopPlay(); play.textContent = "Play"; return; }
    play.textContent = "Pause";
    playTimer = setInterval(() => {
      const n = nextIndex(sel.selectedIndex, monthList.length);
      if (n === null || !document.getElementById("play")) { stopPlay(); play.textContent = "Play"; return; }
      setMonth(n);
      show();
    }, PLAY_MS);
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); show(); });
  if (sel.options.length) { setMonth(0); await show(); }
}

function navigate(view, key) { location.hash = `#/${view}?key=${encodeURIComponent(key)}`; }

function drawGrid(out, rows, r, v, prov) {
  const lons = [...new Set(rows.map((x) => x.longitude))].sort((a, b) => a - b), lats = [...new Set(rows.map((x) => x.latitude))].sort((a, b) => a - b);
  if (!lons.length) return out.append(empty("No cells in this box and month.", "Choose another month or a larger box, then press Show month."));
  const stepOf = (a) => (a.length > 1 ? Math.min(...a.slice(1).map((t, i) => t - a[i])) : 0.25);      // the grid step is read from the data
  const dx = stepOf(lons), dy = stepOf(lats);
  const vals = rows.map((x) => x[v]), ve = extent(vals) || [0, 1];
  const xa = niceAxis(lons[0] - dx / 2, lons[lons.length - 1] + dx / 2, 5), ya = niceAxis(lats[0] - dy / 2, lats[lats.length - 1] + dy / 2, 5);
  const x = linear(xa.min, xa.max, M.l, W - M.r), y = linear(ya.min, ya.max, H - M.b, M.t);
  const svg = frame(`Monthly mean ${v}`, `${rows.length} grid cells coloured by value`);
  axes(svg, { ticks: xa.ticks, scale: x, format: (t) => `${fmt(t, 5)}°` }, { ticks: ya.ticks, scale: y, format: (t) => `${fmt(t, 5)}°` }, { x: "longitude", y: "latitude" });
  const rects = [];
  rows.forEach((c, i) => {
    const col = colour(c[v], ve[0], ve[1]);
    const rect = s("rect", { class: col ? "cell" : "cell empty", "data-i": i, x: x(c.longitude - dx / 2), y: y(c.latitude + dy / 2), width: x(c.longitude + dx / 2) - x(c.longitude - dx / 2), height: y(c.latitude - dy / 2) - y(c.latitude + dy / 2), fill: col || "none" },
      s("title", {}, `${c.latitude}, ${c.longitude}: ${fmt(c[v], 4)} ${r.units[v] || ""}`));
    rects.push(rect);
    svg.append(rect);
  });
  const readout = h("div", { class: "readout", role: "status", id: "cell-readout" }, "Point at a cell, or focus the map and use the arrow keys, to read it.");
  let cur = null;
  const pick = (i) => {
    rects.forEach((e) => e.classList.remove("sel"));
    cur = i;
    rects[i].classList.add("sel");
    readout.textContent = cellReadout(rows[i], v, r.units[v] || "");
  };
  svg.addEventListener("pointerover", (ev) => { const c = ev.target.closest?.("rect.cell"); if (c) pick(Number(c.getAttribute("data-i"))); });
  svg.setAttribute("tabindex", "0");
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", `Monthly mean ${v}: ${rows.length} cells. Use the arrow keys to move from cell to cell.`);
  const at = (lon, lat) => rows.findIndex((c) => c.longitude === lon && c.latitude === lat);
  svg.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[e.key];
    if (!step) return;
    e.preventDefault();
    if (cur === null) return pick(0);
    const li = lons.indexOf(rows[cur].longitude) + step[0], ai = lats.indexOf(rows[cur].latitude) + step[1];
    const next = li >= 0 && li < lons.length && ai >= 0 && ai < lats.length ? at(lons[li], lats[ai]) : -1;
    if (next >= 0) pick(next);
  });
  out.append(svg, readout, colourBar(ve, r.units[v] || ""), prov, h("p", { class: "hint" }, "A cell with too few observations has no value and stays empty (outlined)."),
    tableBlock("Grid cells", ["latitude", "longitude", v, `${v}_n_obs`].filter((c) => r.columns.includes(c)), rows, r.units));
}

function colourBar([lo, hi], unit) {
  const svg = s("svg", { viewBox: "0 0 760 70", class: "chart bar", role: "img", "aria-label": `Colour scale from ${fmt(lo)} to ${fmt(hi)} ${unit}` });
  for (let i = 0; i < 60; i++) svg.append(s("rect", { x: 64 + i * 10, y: 6, width: 10, height: 26, fill: colour(lo + ((hi - lo) * i) / 59, lo, hi) }));
  svg.append(s("rect", { class: "plot", x: 64, y: 6, width: 600, height: 26, fill: "none" }));           // a frame, so the pale end of the scale is still seen against the page
  svg.append(s("text", { class: "label", x: 64, y: 54 }, `min ${fmt(lo)} ${unit}`), s("text", { class: "label", x: 664, y: 54, "text-anchor": "end" }, `max ${fmt(hi)} ${unit}`));
  return svg;
}

// ---- 5. explore and download -------------------------------------------------------------------------------------
const FILE_TYPES = {
  csv: ["CSV", "Plain text, comma separated. A cell that starts with =, +, - or @ is made safe so a spreadsheet will not run it as a formula."],
  netcdf: ["NetCDF", "A binary array file with the variables, units and flags, for scientific software."],
};

export async function exploreView(root, ctx, params) {
  const msg = messages(root);
  const type0 = params.get("type") === "product" ? "product" : "dataset";
  const step = (n, name, ...kids) => h("li", { class: "step" }, h("div", { class: "stephead" }, h("span", { class: "stepno", "aria-hidden": "true" }, String(n)), h("span", {}, `Step ${n}: ${name}`)), ...kids);
  const form = h("form", { class: "filters", novalidate: true },
    h("ol", { class: "steps" },
      step(1, "Choose", field("Collection type", select("type", [["dataset", "datasets"], ["product", "derived products"]], type0)), field("Collection", select("key", [], ""))),
      step(2, "Filter", field("Variable", input("variable")), field("Box west,south,east,north", input("bbox")),
        field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
        field("Depth min (m)", input("depth_min")), field("Depth max (m)", input("depth_max")), field("Platform", input("platform")),
        field("Quality", select("quality", [["good", "good (flags 1, 2)"], ["all", "all, with flags"]], "good")),
        h("label", { class: "check" }, h("input", { type: "checkbox", id: "allow_stale" }), " read a stale product anyway")),
      step(3, "Preview", h("button", { type: "submit" }, "Preview first page"))));
  const out = h("div", { class: "result" });
  const limitsBox = h("details", { class: "limits" }, h("summary", {}, "Your limits"));
  layout(root, "Choose and filter", form, h("div", {}, out, limitsBox));
  let items = [];
  async function loadItems() {
    const type = val("type");
    try {
      items = (await get(type === "product" ? "/products" : "/datasets", { page_size: ctx.pageSize, include_stale: "true" })).items;
    } catch (e) { return msg.error(e); }
    const sel = document.getElementById("key");
    clear(sel);
    const kf = type === "product" ? "product_key" : "dataset_key";
    items.forEach((d) => sel.append(h("option", { value: d[kf], ...(d[kf] === params.get("key") ? { selected: true } : {}) }, `${d[kf]}${d.stale ? " (stale)" : ""}`)));
  }
  document.getElementById("type").addEventListener("change", loadItems);
  await loadItems();
  const lim = await ctx.limits();
  limitsBox.append(h("dl", { class: "citation" }, Object.entries(lim).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))])));
  const bounds = (name, lo, hi) => { const n = val(name).trim(); if (n !== "" && !(Number.isFinite(Number(n)) && Number(n) >= lo && Number(n) <= hi)) throw new Error(`${name} must be a number from ${lo} to ${hi}`); return n; };
  function params_() {
    const type = val("type");
    const box = val("bbox").trim() ? parseBox(val("bbox")) : null;
    if (box && box.error) throw new Error(box.error);
    return {
      type, key: val("key"),
      q: { variable: val("variable"), bbox: box ? boxParam(box) : "", time_from: val("time_from"), time_to: val("time_to"), depth_min: bounds("depth_min", 0, 20000), depth_max: bounds("depth_max", 0, 20000), platform: val("platform"), quality: type === "product" ? "" : val("quality"), allow_stale: document.getElementById("allow_stale").checked ? "true" : "" },
    };
  }
  const base = (p) => `/${p.type === "product" ? "products" : "datasets"}/${encodeURIComponent(p.key)}`;
  let seq = 0;
  async function preview(page = 1) {
    const mine = ++seq;
    msg.clear(); busy(out);
    let p;
    try { p = params_(); } catch (e) { clear(out); return msg.error(e); }
    const it = items.find((d) => (d.product_key || d.dataset_key) === p.key);
    ctx.setSynthetic(it ? ctx.isSynthetic(it.dataset_key) : false);
    try {
      const r = await get(`${base(p)}/subset`, { ...p.q, page, page_size: Math.min(50, ctx.pageSize) });
      if (mine !== seq) return;
      clear(out);
      msg.notes(notices(r));
      if (!r.n_rows_selected) { out.append(empty("No rows match this selection.", "Widen the dates, remove the box or the depth limits, or choose another collection.")); return; }
      const pages = Math.max(1, Math.ceil(r.n_rows_selected / r.page_size));
      const prov = provenance(r, "Selection", p.key, it ? ctx.isSynthetic(it.dataset_key) : false, p.type === "product" ? null : val("quality"));
      out.append(h("p", { class: "status", id: "explore-status", role: "status" }, `Page ${r.page} of ${pages} (${r.n_rows_selected} rows selected)`),
        h("p", { class: "count", id: "explore-count" }, `${r.n_rows_selected} rows selected, ${r.rows.length} shown (page ${r.page})`));
      if (r.next_page) out.append(h("p", { class: "buttons" }, h("button", { type: "button", onclick: () => preview(r.next_page) }, `Next page (${r.next_page})`)));
      out.append(h("div", { class: "stephead" }, h("span", { class: "stepno", "aria-hidden": "true" }, "4"), h("span", {}, "Step 4: Download")),
        h("div", { class: "dlcards" }, ["csv", "netcdf"].map((f) => {
          const b = h("button", { type: "button", class: "dl primary", "data-format": f }, `Download ${FILE_TYPES[f][0]}`);
          b.addEventListener("click", () => save(f));
          return h("div", { class: "dlcard" }, h("span", { class: "ftype" }, FILE_TYPES[f][0]), h("p", {}, FILE_TYPES[f][1]),
            h("p", { class: "dlrows" }, `${r.n_rows_selected} rows selected`), h("p", { class: "dllimit" }, `Rows limit for a download: ${lim.max_rows_download}`), b);
        })),
        h("p", { id: "saved", role: "status" }), prov, tableBlock("First page of the selection", r.columns, r.rows, r.units));
      out.querySelector(".tableblock button").click();
    } catch (e) { if (mine === seq) { clear(out); msg.error(e); } }
  }
  async function save(format) {
    msg.clear();
    try {
      const p = params_();
      const { blob, name } = await download(`${base(p)}/download`, { ...p.q, format });
      const a = h("a", { href: URL.createObjectURL(blob), download: name });
      document.body.append(a);
      a.click();
      a.remove();
      document.getElementById("saved").textContent = `Saved ${name} (${blob.size} bytes). The name was chosen by the server.`;
    } catch (e) { msg.error(e); }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); preview(1); });
}
