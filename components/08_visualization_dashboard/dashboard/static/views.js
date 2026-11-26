// The five views. They show what the API returns: no smoothing, no filling. A missing value is a gap, never a zero.
import { download, get } from "./api.js";
import {
  bandRuns, baseVariables, boxParam, columnsOf, citation, colour, extent, flagInfo, fmt, isNum, linear, lonSpans, monthRange, months, niceAxis,
  notices, oneProfile, parseBox, plusSeconds, profileList, runs, timeAxis, utc,
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

function citationBox(info) {
  const c = citation(info);
  return h("dl", { class: "citation", "aria-label": "How to cite this data" },
    h("dt", {}, "data_version"), h("dd", {}, c.data_version),
    h("dt", {}, "product_version"), h("dd", {}, c.product_version),
    h("dt", {}, "qc_config_sha256"), h("dd", {}, c.qc_config_sha256),
    h("dt", {}, "flags used"), h("dd", {}, c.flags_used));
}

// A chart is always paired with its numbers: a button toggles a table.
function tableBlock(caption, columns, rows, units = {}) {
  const wrap = h("div", { class: "tableblock" });
  const tbl = h("table", { hidden: true, id: `t${Math.random().toString(36).slice(2, 8)}` },
    h("caption", {}, caption),
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col" }, units[c] ? `${c} (${units[c]})` : c)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, columns.map((c) => h("td", {}, c.endsWith("_qc") && r[c] !== null && r[c] !== undefined ? `${r[c]} ${flagInfo(r[c]).label}` : isNum(r[c]) ? fmt(r[c], 4) : r[c] === null || r[c] === undefined ? "no value" : String(r[c])))))));
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
export async function mapView(root, ctx) {
  const msg = messages(root);
  const form = h("form", { class: "filters", novalidate: true },
    field("Search text", input("q")), field("Platform type", input("platform_type", "", { placeholder: "e.g. argo_float" })),
    field("Variable", input("variable", "", { placeholder: "e.g. temperature" })),
    field("Box west,south,east,north", input("bbox", "", { placeholder: "60,5,80,25" })),
    field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
    h("button", { type: "submit" }, "Apply filters"));
  const out = h("div", { class: "result" });
  root.append(h("p", {}, "Footprints of published datasets on a drawn graticule. No coastline or tiles are drawn (offline demo)."), form, out);
  let seq = 0;                                           // only the newest request may draw: an older answer arriving late is dropped
  async function load() {
    const mine = ++seq;
    msg.clear();
    clear(out);
    const box = val("bbox").trim() ? parseBox(val("bbox")) : null;
    if (box && box.error) return msg.error(new Error(box.error));
    try {
      const r = await datasetsOf(ctx, { q: val("q"), platform_type: val("platform_type"), variable: val("variable"), bbox: box ? boxParam(box) : "", time_from: val("time_from"), time_to: val("time_to") });
      if (mine !== seq) return;
      msg.notes(notices(r));
      drawMap(out, r.items, box, ctx);
    } catch (e) { msg.error(e); }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); load(); });
  await load();
}

function drawMap(out, items, box, ctx) {
  ctx.setSynthetic(items.length > 0 && items.every((d) => d.is_synthetic));
  const withBox = items.filter((d) => [d.lat_min, d.lat_max, d.lon_min, d.lon_max].every(isNum));
  out.append(h("p", { class: "count", id: "map-count" }, `${items.length} dataset${items.length === 1 ? "" : "s"} shown`));
  if (!withBox.length) return;
  const pieces = withBox.flatMap((d) => lonSpans(d.lon_min, d.lon_max).map(([a, b]) => ({ d, a, b })));
  const lonE = extent(pieces.flatMap((p) => [p.a, p.b]).concat(box ? [box.west, box.east] : []));
  const latE = extent(withBox.flatMap((d) => [d.lat_min, d.lat_max]).concat(box ? [box.south, box.north] : []));
  const pad = (e, m) => [e[0] - Math.max(m, (e[1] - e[0]) * 0.1), e[1] + Math.max(m, (e[1] - e[0]) * 0.1)];
  const lonD = pad(lonE, 3), latD = pad(latE, 3);
  const xa = niceAxis(lonD[0], lonD[1], 6), ya = niceAxis(latD[0], latD[1], 5);
  const x = linear(xa.min, xa.max, M.l, W - M.r), y = linear(ya.min, ya.max, H - M.b, M.t);
  const svg = frame("Map of dataset footprints", `${items.length} datasets drawn as rectangles or points on a longitude and latitude graticule`);
  axes(svg, { ticks: xa.ticks, scale: x, format: (t) => `${fmt(t, 4)}°` }, { ticks: ya.ticks, scale: y, format: (t) => `${fmt(t, 4)}°` }, { x: "longitude", y: "latitude" });
  if (box) {
    const spans = box.wraps ? [[box.west, 180], [-180, box.east]] : [[box.west, box.east]];
    for (const [a, b] of spans) svg.append(s("rect", { class: "bbox", x: x(a), y: y(box.north), width: Math.max(1, x(b) - x(a)), height: Math.max(1, y(box.south) - y(box.north)) }));
  }
  for (const p of pieces) {
    const d = p.d, w = x(p.b) - x(p.a), ht = y(d.lat_min) - y(d.lat_max);
    const cx = (x(p.a) + x(p.b)) / 2, cy = (y(d.lat_min) + y(d.lat_max)) / 2;
    const g = s("g", { class: `fp type-${String(d.dataset_type).replace(/[^a-z0-9_]/gi, "")}`, tabindex: 0, role: "img", "aria-label": `${d.dataset_key}: ${d.platform_type}` }, s("title", {}, `${d.dataset_key} (${d.platform_type})`));
    if (w < 3 && ht < 3) g.append(marker("diamond", cx, cy, "fpmark"));
    else g.append(s("rect", { class: "fprect", x: x(p.a) - (w < 3 ? 3 : 0), y: y(d.lat_max) - (ht < 3 ? 3 : 0), width: Math.max(6, w), height: Math.max(6, ht) }));
    svg.append(g);
  }
  out.append(svg);
  const cols = ["dataset_key", "title", "dataset_type", "platform_type", "platform_id", "time_start", "time_end", "lat_min", "lat_max", "lon_min", "lon_max"];
  out.append(h("table", { class: "list" }, h("caption", {}, "Datasets"), h("thead", {}, h("tr", {}, cols.map((c) => h("th", { scope: "col" }, c)), h("th", { scope: "col" }, "open in"))),
    h("tbody", {}, items.map((d) => h("tr", {}, cols.map((c) => h("td", {}, isNum(d[c]) ? fmt(d[c], 4) : c.startsWith("time_") ? utc(d[c]) : d[c] === null ? "no value" : String(d[c]))),
      h("td", {}, h("a", { href: `#/explore?type=dataset&key=${encodeURIComponent(d.dataset_key)}` }, "Explore"), " ",
        d.dataset_type === "profile" ? h("a", { href: `#/profile?key=${encodeURIComponent(d.dataset_key)}` }, "Profile") : null))))));
}

// ---- 2. profile --------------------------------------------------------------------------------------------------
export async function profileView(root, ctx, params) {
  const msg = messages(root);
  let list;
  try { list = (await datasetsOf(ctx, { dataset_type: "profile" })).items; } catch (e) { return msg.error(e); }
  if (!list.length) return root.append(h("p", {}, "There are no published profile datasets."));
  const form = h("form", { class: "filters", novalidate: true },
    field("Dataset", select("dataset", list.map((d) => [d.dataset_key, d.dataset_key]), params.get("key") || list[0].dataset_key)),
    field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
    field("Platform", input("platform")), h("button", { type: "submit" }, "Find profiles"));
  const pick = h("div", { class: "filters pick" });
  const out = h("div", { class: "result" });
  root.append(form, pick, out);
  let profiles = [];
  let findSeq = 0, showSeq = 0;
  async function find() {
    const mine = ++findSeq;
    showSeq++;
    msg.clear(); clear(pick); clear(out);
    const key = val("dataset");
    ctx.setSynthetic(list.find((d) => d.dataset_key === key)?.is_synthetic === true);
    try {
      const r = await get(`/datasets/${encodeURIComponent(key)}/subset`, { depth_max: 6, page_size: ctx.pageSize, time_from: val("time_from"), time_to: val("time_to"), platform: val("platform") });
      if (mine !== findSeq) return;
      msg.notes(notices(r));
      profiles = profileList(r.rows);
      if (!profiles.length) return pick.append(h("p", {}, "No profiles match these filters."));
      const sel = select("profile", profiles.map((p, i) => [String(i), `${p.platform_id} #${p.source_record}  ${p.time}  (${fmt(p.latitude, 4)}, ${fmt(p.longitude, 4)})`]), "0");
      const q = select("quality", [["good", "good values only (flags 1, 2)"], ["all", "all values with their flags"]], "good");
      sel.addEventListener("change", show);
      q.addEventListener("change", show);
      pick.append(h("p", { class: "count" }, `${profiles.length} profile${profiles.length === 1 ? "" : "s"} listed`), field("Profile", sel), field("Quality", q));
    } catch (e) { msg.error(e); return; }
    if (mine === findSeq) await show();
  }
  async function show() {
    const mine = ++showSeq;
    msg.clear(); clear(out);
    const p = profiles[Number(val("profile"))];
    const key = val("dataset");
    try {
      const r = await get(`/datasets/${encodeURIComponent(key)}/subset`, { platform: p.platform_id, time_from: p.time, time_to: plusSeconds(p.time, 1), quality: val("quality"), page_size: ctx.pageSize });
      if (mine !== showSeq) return;
      msg.notes(notices(r));
      const rows = oneProfile(r.rows, p);
      out.append(h("h3", {}, `${p.platform_id} #${p.source_record}, ${p.time}`), citationBox(r));
      const vars = r.columns.filter((c) => !c.endsWith("_qc") && !["platform_id", "source_record", "time", "latitude", "longitude", "level", "depth"].includes(c));
      const row = h("div", { class: "panels" });
      for (const v of vars) row.append(profilePanel(v, rows, r.units[v]));
      out.append(row, legend(), tableBlock("Profile values", ["depth", ...vars.flatMap((v) => [v, `${v}_qc`])], rows, r.units));
    } catch (e) { msg.error(e); }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); find(); });
  await find();
}

function profilePanel(variable, rows, unit) {
  const xs = rows.map((r) => r[variable]), ds = rows.map((r) => r.depth);
  const w = 360, ht = 340, m = { l: 56, r: 14, t: 14, b: 44 };
  const svg = s("svg", { viewBox: `0 0 ${w} ${ht}`, class: "chart panel", role: "img", "aria-label": `${variable} against depth` }, s("title", {}, `${variable} against depth`));
  const xe = extent(xs), de = extent(ds);
  if (!xe || !de) { svg.append(s("text", { class: "label", x: w / 2, y: ht / 2, "text-anchor": "middle" }, `${variable}: no values`)); return svg; }
  const xa = niceAxis(xe[0], xe[1], 4), da = niceAxis(0, de[1], 5);
  const x = linear(xa.min, xa.max, m.l, w - m.r), y = linear(da.min, da.max, m.t, ht - m.b);        // depth grows downwards
  for (const t of xa.ticks) svg.append(s("line", { class: "grid", x1: x(t), x2: x(t), y1: m.t, y2: ht - m.b }), s("text", { class: "tick", x: x(t), y: ht - m.b + 15, "text-anchor": "middle" }, fmt(t)));
  for (const t of da.ticks) svg.append(s("line", { class: "grid", x1: m.l, x2: w - m.r, y1: y(t), y2: y(t) }), s("text", { class: "tick", x: m.l - 5, y: y(t) + 4, "text-anchor": "end" }, fmt(t)));
  svg.append(s("rect", { class: "plot", x: m.l, y: m.t, width: w - m.l - m.r, height: ht - m.t - m.b, fill: "none" }),
    s("text", { class: "label", x: (m.l + w - m.r) / 2, y: ht - 6, "text-anchor": "middle" }, `${variable}${unit ? ` (${unit})` : ""}`),
    s("text", { class: "label", x: 12, y: (m.t + ht - m.b) / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${(m.t + ht - m.b) / 2})` }, "depth (m)"));
  for (const run of runs(xs, ds)) svg.append(s("path", { class: "line", d: path(run.map(([a, b]) => [x(a), y(b)])) }));
  rows.forEach((r) => {
    const f = flagInfo(r[`${variable}_qc`]);
    if (isNum(r[variable]) && isNum(r.depth)) svg.append(marker(f.shape, x(r[variable]), y(r.depth), `flag${f.code ?? "none"}`));
  });
  return svg;
}

function legend() {
  const svg = s("svg", { viewBox: "0 0 760 30", class: "legend", role: "img", "aria-label": "Flag marker shapes" });
  [1, 2, 3, 4, 9].forEach((f, i) => {
    const x = 20 + i * 150, info = flagInfo(f);
    svg.append(marker(info.shape, x, 15, `flag${f}`), s("text", { class: "tick", x: x + 12, y: 19 }, `${f} ${info.label}`));
  });
  return svg;
}

// ---- 3. series ---------------------------------------------------------------------------------------------------
export async function seriesView(root, ctx, params) {
  const msg = messages(root);
  let list;
  try { list = (await get("/products", { page_size: ctx.pageSize, include_stale: "true" })).items.filter((p) => p.kind === "series_hourly" || p.kind === "series_daily"); } catch (e) { return msg.error(e); }
  if (!list.length) return root.append(h("p", {}, "There are no series products built yet."));
  const form = h("form", { class: "filters", novalidate: true },
    field("Product", select("product", list.map((p) => [p.product_key, `${p.product_key}${p.stale ? " (stale)" : ""}`]), params.get("key") || list[0].product_key)),
    field("Variable", select("variable", [], "")), field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
    field("Platform", input("platform")), h("label", { class: "check" }, h("input", { type: "checkbox", id: "allow_stale" }), " read a stale product anyway"),
    h("button", { type: "submit" }, "Show series"));
  const out = h("div", { class: "result" });
  root.append(form, out);
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
    msg.clear(); clear(out);
    const p = list.find((x) => x.product_key === val("product"));
    ctx.setSynthetic(ctx.isSynthetic(p.dataset_key));
    try {
      const q = { variable: columnsOf(p.variables, val("variable")).join(","), time_from: val("time_from"), time_to: val("time_to"), platform: val("platform"), page_size: ctx.pageSize, allow_stale: document.getElementById("allow_stale").checked ? "true" : "" };
      const page = (n) => get(`/products/${encodeURIComponent(p.product_key)}/subset`, { ...q, page: n });
      const r = await page(1);
      if (mine !== seq) return;
      msg.notes(notices(r));
      await drawSeries(out, r, val("variable"), ctx, page, () => mine !== seq);
    } catch (e) { msg.error(e); }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); show(); });
  await show();
}

async function drawSeries(out, r, v, ctx, page, cancelled) {
  out.append(citationBox(r));
  // the API pages: follow the pages until the rows of the selection are all here, within the user's limits
  let rows = r.rows, resp = r;
  while (resp.truncated && rows.length < ctx.maxSeriesRows) {
    resp = await page(resp.next_page);
    if (cancelled()) return;
    rows = rows.concat(resp.rows);
  }
  if (resp.truncated) out.append(h("p", { class: "notice", role: "status" }, `Only the first ${rows.length} of ${r.n_rows_selected} rows are drawn. Narrow the time range.`));
  const times = rows.map((x) => Date.parse(x.time));
  const mean = rows.map((x) => x[`${v}_mean`]), lo = rows.map((x) => x[`${v}_min`]), hi = rows.map((x) => x[`${v}_max`]), cnt = rows.map((x) => x[`${v}_count`]);
  const te = extent(times), ve = extent(mean.concat(lo, hi));
  if (!te || !ve) return out.append(h("p", {}, `No values of ${v} in this selection.`));
  const xa = timeAxis(te[0], te[1], 6), ya = niceAxis(ve[0], ve[1], 5);
  const span = te[1] - te[0];
  const svg = frame(`${v} over time`, `${rows.length} bins, mean line with minimum and maximum band`);
  const x = linear(xa.min, xa.max, M.l, W - M.r), y = linear(ya.min, ya.max, H - M.b, M.t);
  axes(svg, { ticks: xa.ticks, scale: x, format: timeTick(span) }, { ticks: ya.ticks, scale: y }, { x: "time (UTC)", y: `${v} (${r.units[`${v}_mean`] || ""})` });
  for (const run of bandRuns(times, lo, hi)) svg.append(s("path", { class: "band", d: `${path(run.map(([t, a]) => [x(t), y(a)]))}${path(run.slice().reverse().map(([t, , b]) => [x(t), y(b)])).replace("M", "L")}Z` }));
  for (const run of runs(times, mean)) svg.append(s("path", { class: "line", d: path(run.map(([t, a]) => [x(t), y(a)])) }));
  out.append(svg);
  const ce = extent(cnt);
  if (ce) {
    const ca = niceAxis(0, ce[1], 2), cy = linear(ca.min, ca.max, 70, 8);
    const bars = s("svg", { viewBox: `0 0 ${W} 80`, class: "chart counts", role: "img", "aria-label": `Number of observations in each bin of ${v}` }, s("title", {}, `Observations per bin (${v})`));
    rows.forEach((row, i) => { if (isNum(cnt[i])) bars.append(s("rect", { class: "bar", x: x(times[i]) - 1, y: cy(cnt[i]), width: 2, height: Math.max(0, cy(0) - cy(cnt[i])) })); });
    bars.append(s("text", { class: "tick", x: 8, y: 12 }, `count per bin, 0 to ${fmt(ca.max)}`));
    out.append(bars);
  }
  out.append(h("p", { class: "hint" }, "Band: minimum to maximum within each bin. A bin with too few good values is drawn as a gap."),
    tableBlock("Series values", ["time", `${v}_mean`, `${v}_min`, `${v}_max`, `${v}_count`].filter((c) => r.columns.includes(c)), rows, r.units));
}

// ---- 4. SST ------------------------------------------------------------------------------------------------------
export async function sstView(root, ctx, params) {
  const msg = messages(root);
  let list;
  try { list = (await get("/products", { page_size: ctx.pageSize, kind: "sst_monthly_mean", include_stale: "true" })).items; } catch (e) { return msg.error(e); }
  if (!list.length) return root.append(h("p", {}, "There is no monthly SST product built yet."));
  const p0 = list.find((p) => p.product_key === params.get("key")) || list[0];
  const form = h("form", { class: "filters", novalidate: true },
    field("Product", select("product", list.map((p) => [p.product_key, p.product_key]), p0.product_key)),
    field("Month", select("month", months(p0.time_start, p0.time_end).map((m) => [m, m]), "")),
    field("Box west,south,east,north", input("bbox", `${p0.lon_min},${p0.lat_min},${Math.min(p0.lon_max, p0.lon_min + 3)},${Math.min(p0.lat_max, p0.lat_min + 3)}`)),
    h("label", { class: "check" }, h("input", { type: "checkbox", id: "allow_stale" }), " read a stale product anyway"), h("button", { type: "submit" }, "Show month"));
  const out = h("div", { class: "result" });
  root.append(form, out);
  let seq = 0;
  async function show() {
    const mine = ++seq;
    msg.clear(); clear(out);
    const p = list.find((x) => x.product_key === val("product"));
    const box = parseBox(val("bbox"));
    const range = monthRange(val("month"));
    if (box.error) return msg.error(new Error(box.error));
    if (!range) return msg.error(new Error("choose a month"));
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
      if (resp.truncated) out.append(h("p", { class: "notice", role: "status" }, `Only the first ${rows.length} of ${r.n_rows_selected} cells are drawn. Use a smaller box.`));
      out.append(citationBox(r));
      drawGrid(out, rows, r, p.variables[0]);
    } catch (e) { msg.error(e); }
  }
  document.getElementById("product").addEventListener("change", () => navigate("sst", val("product")));
  form.addEventListener("submit", (e) => { e.preventDefault(); show(); });
  const sel = document.getElementById("month");
  if (sel.options.length) { sel.selectedIndex = 0; await show(); }
}

function navigate(view, key) { location.hash = `#/${view}?key=${encodeURIComponent(key)}`; }

function drawGrid(out, rows, r, v) {
  const lons = [...new Set(rows.map((x) => x.longitude))].sort((a, b) => a - b), lats = [...new Set(rows.map((x) => x.latitude))].sort((a, b) => a - b);
  if (!lons.length) return out.append(h("p", {}, "No cells in this box and month."));
  const stepOf = (a) => (a.length > 1 ? Math.min(...a.slice(1).map((t, i) => t - a[i])) : 0.25);      // the grid step is read from the data
  const dx = stepOf(lons), dy = stepOf(lats);
  const vals = rows.map((x) => x[v]), ve = extent(vals) || [0, 1];
  const xa = niceAxis(lons[0] - dx / 2, lons[lons.length - 1] + dx / 2, 5), ya = niceAxis(lats[0] - dy / 2, lats[lats.length - 1] + dy / 2, 5);
  const x = linear(xa.min, xa.max, M.l, W - M.r), y = linear(ya.min, ya.max, H - M.b, M.t);
  const svg = frame(`Monthly mean ${v}`, `${rows.length} grid cells coloured by value`);
  axes(svg, { ticks: xa.ticks, scale: x, format: (t) => `${fmt(t, 5)}°` }, { ticks: ya.ticks, scale: y, format: (t) => `${fmt(t, 5)}°` }, { x: "longitude", y: "latitude" });
  rows.forEach((c) => {
    const col = colour(c[v], ve[0], ve[1]);
    const rect = s("rect", { class: col ? "cell" : "cell empty", x: x(c.longitude - dx / 2), y: y(c.latitude + dy / 2), width: x(c.longitude + dx / 2) - x(c.longitude - dx / 2), height: y(c.latitude - dy / 2) - y(c.latitude + dy / 2), fill: col || "none" },
      s("title", {}, `${c.latitude}, ${c.longitude}: ${fmt(c[v], 4)} ${r.units[v] || ""}`));
    svg.append(rect);
  });
  out.append(svg, colourBar(ve, r.units[v] || ""), h("p", { class: "hint" }, "A cell with too few observations has no value and stays empty (outlined)."),
    tableBlock("Grid cells", ["latitude", "longitude", v, `${v}_n_obs`].filter((c) => r.columns.includes(c)), rows, r.units));
}

function colourBar([lo, hi], unit) {
  const svg = s("svg", { viewBox: "0 0 760 44", class: "chart bar", role: "img", "aria-label": `Colour scale from ${fmt(lo)} to ${fmt(hi)} ${unit}` });
  for (let i = 0; i < 60; i++) svg.append(s("rect", { x: 64 + i * 10, y: 4, width: 10, height: 14, fill: colour(lo + ((hi - lo) * i) / 59, lo, hi) }));
  svg.append(s("text", { class: "tick", x: 64, y: 36 }, `${fmt(lo)} ${unit}`), s("text", { class: "tick", x: 664, y: 36, "text-anchor": "end" }, `${fmt(hi)} ${unit}`));
  return svg;
}

// ---- 5. explore and download -------------------------------------------------------------------------------------
export async function exploreView(root, ctx, params) {
  const msg = messages(root);
  const type0 = params.get("type") === "product" ? "product" : "dataset";
  const form = h("form", { class: "filters", novalidate: true },
    field("Collection type", select("type", [["dataset", "datasets"], ["product", "derived products"]], type0)),
    field("Collection", select("key", [], "")), field("Variable", input("variable")), field("Box west,south,east,north", input("bbox")),
    field("From", input("time_from", "", { type: "date" })), field("To", input("time_to", "", { type: "date" })),
    field("Depth min (m)", input("depth_min")), field("Depth max (m)", input("depth_max")), field("Platform", input("platform")),
    field("Quality", select("quality", [["good", "good (flags 1, 2)"], ["all", "all, with flags"]], "good")),
    h("label", { class: "check" }, h("input", { type: "checkbox", id: "allow_stale" }), " read a stale product anyway"),
    h("button", { type: "submit" }, "Preview first page"));
  const out = h("div", { class: "result" });
  const limitsBox = h("details", { class: "limits" }, h("summary", {}, "Your limits"));
  root.append(form, out, limitsBox);
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
    msg.clear(); clear(out);
    let p;
    try { p = params_(); } catch (e) { return msg.error(e); }
    const it = items.find((d) => (d.product_key || d.dataset_key) === p.key);
    ctx.setSynthetic(it ? ctx.isSynthetic(it.dataset_key) : false);
    try {
      const r = await get(`${base(p)}/subset`, { ...p.q, page, page_size: Math.min(50, ctx.pageSize) });
      if (mine !== seq) return;
      msg.notes(notices(r));
      out.append(h("p", { class: "count", id: "explore-count" }, `${r.n_rows_selected} rows selected, ${r.rows.length} shown (page ${r.page})`), citationBox(r),
        tableBlock("First page of the selection", r.columns, r.rows, r.units));
      out.querySelector(".tableblock button").click();
      const nav = h("p", { class: "buttons" });
      if (r.next_page) nav.append(h("button", { type: "button", onclick: () => preview(r.next_page) }, `Next page (${r.next_page})`));
      for (const f of ["csv", "netcdf"]) nav.append(h("button", { type: "button", class: "dl", "data-format": f, onclick: () => save(f) }, `Download ${f === "csv" ? "CSV" : "NetCDF"}`));
      out.append(nav, h("p", { id: "saved", role: "status" }));
    } catch (e) { msg.error(e); }
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
