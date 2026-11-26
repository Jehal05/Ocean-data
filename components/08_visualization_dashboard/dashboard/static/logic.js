// Pure functions of the dashboard (no DOM, no network), so they can be tested with `node --test`.
// Rule: show what the API returned. Nothing here smooths, interpolates or fills a value.

export const FLAGS = {
  0: { label: "not checked", shape: "square" },
  1: { label: "good", shape: "circle" },
  2: { label: "probably good", shape: "diamond" },
  3: { label: "probably bad", shape: "triangle" },
  4: { label: "bad", shape: "cross" },
  9: { label: "missing", shape: "ring" },
};

// Label and marker shape of a QC flag. An unknown value is named as unknown, never shown as good.
export function flagInfo(flag) {
  if (flag === null || flag === undefined || Number.isNaN(Number(flag))) return { code: null, label: "no flag", shape: "ring" };
  const f = FLAGS[Number(flag)];
  return f ? { code: Number(flag), ...f } : { code: Number(flag), label: `unknown flag ${Number(flag)}`, shape: "ring" };
}

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

// Text for a value: a missing value is said to be missing (never "0").
export function fmt(v, digits = 3) {
  if (!isNum(v)) return "no value";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return v.toExponential(digits - 1);
  return String(Number(v.toFixed(digits)));
}

// "Nice" axis: ticks at 1, 2, 5 times a power of ten that cover [min, max] with about n intervals.
export function niceAxis(min, max, n = 5) {
  if (!isNum(min) || !isNum(max)) return null;
  if (min === max) { const d = min === 0 ? 1 : Math.abs(min) * 0.1; min -= d; max += d; }
  const raw = (max - min) / n;
  const p = 10 ** Math.floor(Math.log10(raw));
  const m = raw / p;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
  const lo = Math.floor(min / step + 1e-9) * step;
  const hi = Math.ceil(max / step - 1e-9) * step;
  const ticks = [];
  for (let i = 0; lo + i * step <= hi + step * 1e-9; i++) ticks.push(Number((lo + i * step).toPrecision(12)));
  return { min: ticks[0], max: ticks[ticks.length - 1], step, ticks };
}

// Linear scale from a domain to a pixel range.
export function linear(d0, d1, r0, r1) {
  return (v) => (d1 === d0 ? (r0 + r1) / 2 : r0 + ((v - d0) / (d1 - d0)) * (r1 - r0));
}

// Split paired values into runs of points that all have numbers. A missing value ends a run, so the line has a gap there.
export function runs(xs, ys) {
  const out = [];
  let cur = [];
  for (let i = 0; i < xs.length; i++) {
    if (isNum(xs[i]) && isNum(ys[i])) cur.push([xs[i], ys[i]]);
    else if (cur.length) { out.push(cur); cur = []; }
  }
  if (cur.length) out.push(cur);
  return out;
}

// Runs for a band between two series (min and max): both must be numbers at the same index.
export function bandRuns(xs, lo, hi) {
  const out = [];
  let cur = [];
  for (let i = 0; i < xs.length; i++) {
    if (isNum(xs[i]) && isNum(lo[i]) && isNum(hi[i])) cur.push([xs[i], lo[i], hi[i]]);
    else if (cur.length) { out.push(cur); cur = []; }
  }
  if (cur.length) out.push(cur);
  return out;
}

export const extent = (values) => {
  const v = values.filter(isNum);
  return v.length ? [Math.min(...v), Math.max(...v)] : null;
};

// ---- longitude and boxes ------------------------------------------------------------------------------------------
// Longitudes are put in [-180, 180). A box with west > east crosses the 180 degree line.
export function normLon(lon) {
  const x = ((((lon + 180) % 360) + 360) % 360) - 180;
  return x;
}

// The x-intervals a longitude range covers on a map from -180 to 180. min > max means it crosses 180: two pieces.
export function lonSpans(min, max) {
  const a = normLon(min), b = max === 180 && min !== 180 ? 180 : normLon(max);
  return a <= b ? [[a, b]] : [[a, 180], [-180, b]];
}

// "west,south,east,north" typed by the user -> a box, or an error text. Same limits as the API (it checks again).
export function parseBox(text) {
  const parts = String(text).split(",").map((s) => s.trim());
  if (parts.length !== 4 || parts.some((s) => !/^-?\d+(\.\d+)?$/.test(s))) return { error: "a box is west,south,east,north as plain numbers" };
  const [w, s, e, n] = parts.map(Number);
  if (s < -90 || n > 90 || s > n) return { error: "south must be at least -90, north at most 90, and south not above north" };
  if ([w, e].some((x) => x < -540 || x > 540)) return { error: "longitudes must be between -540 and 540" };
  return { west: normLon(w), south: s, east: e === 180 ? 180 : normLon(e), north: n, wraps: normLon(w) > (e === 180 ? 180 : normLon(e)) };
}

export const boxParam = (b) => `${b.west},${b.south},${b.east},${b.north}`;

// Does a footprint [lonMin..lonMax]x[latMin..latMax] meet a box? (The API does this for real; the map uses it to dim nothing: only for tests of the idea.)
export function overlaps(fp, b) {
  if (fp.latMax < b.south || fp.latMin > b.north) return false;
  const boxSpans = b.wraps ? [[b.west, 180], [-180, b.east]] : [[b.west, b.east]];
  return lonSpans(fp.lonMin, fp.lonMax).some(([a, c]) => boxSpans.some(([x, y]) => a <= y && c >= x));
}

// ---- requests and messages ----------------------------------------------------------------------------------------
// Query string from an object, skipping empty values; keys are sorted so the same filter always gives the same URL.
export function query(params) {
  const sp = new URLSearchParams();
  for (const k of Object.keys(params).sort()) {
    const v = params[k];
    if (v !== undefined && v !== null && String(v).trim() !== "") sp.set(k, String(v).trim());
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

// What the user reads when the API refuses. The API's own text is kept.
export function errorText(status, detail) {
  const d = typeof detail === "string" ? detail : detail && detail.detail ? String(detail.detail) : "";
  const head = {
    401: "Please log in again",
    403: "Not allowed",
    404: "Not found",
    409: "Refused: the data changed or is stale",
    413: "Too much data requested",
    422: "That filter was refused",
    429: "Too many requests, wait a minute",
    502: "The stored data failed its check, nothing was returned",
    503: "The service could not log or reach its database, nothing was returned",
    504: "The request ran out of time",
  }[status] || `Request failed (${status})`;
  return d ? `${head} (HTTP ${status}): ${d}` : `${head} (HTTP ${status})`;
}

// The file name the server chose, from Content-Disposition. Never built from user input here.
export function dispositionName(header) {
  const m = /filename="([A-Za-z0-9._-]{1,200})"/.exec(header || "");
  return m ? m[1] : null;
}

// ---- notices -------------------------------------------------------------------------------------------------------
export function citation(info) {
  return {
    data_version: info.data_version || "unknown",
    product_version: info.product_version === null || info.product_version === undefined ? "none (a dataset)" : String(info.product_version),
    qc_config_sha256: info.qc_config_sha256 || "not recorded",
    flags_used: Array.isArray(info.flags_used) ? info.flags_used.join(", ") : String(info.flags_used ?? "unknown"),
  };
}

export function notices(resp) {
  const out = [];
  if (resp && resp.truncated) {
    const total = resp.n_rows_selected ?? resp.total;
    out.push(`Only part of the selection is shown: page ${resp.page} of ${Math.ceil(total / resp.page_size)} (${total} selected). Go to the next page, narrow the filter, or download.`);
  }
  if (resp && resp.stale) out.push(`This product is stale: ${resp.stale_reason || "its source changed"}. Rebuild it before relying on it.`);
  return out;
}

// ---- grouping and colours -----------------------------------------------------------------------------------------
// One entry per profile (platform_id + source_record) from rows of a profile dataset.
export function profileList(rows) {
  const seen = new Map();
  for (const r of rows) {
    const k = `${r.platform_id}\u0000${r.source_record}`;
    if (!seen.has(k)) seen.set(k, { platform_id: r.platform_id, source_record: r.source_record, time: r.time, latitude: r.latitude, longitude: r.longitude });
  }
  return [...seen.values()].sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : a.source_record - b.source_record));
}

// The rows of one profile, shallowest first.
export const oneProfile = (rows, p) => rows.filter((r) => r.platform_id === p.platform_id && r.source_record === p.source_record).sort((a, b) => a.depth - b.depth);

const STOPS = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];   // viridis, 5 stops
export function colour(v, lo, hi) {
  if (!isNum(v) || !isNum(lo) || !isNum(hi)) return null;                      // no value: no colour, the cell stays empty
  const t = hi === lo ? 0.5 : Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
  const x = t * (STOPS.length - 1), i = Math.min(STOPS.length - 2, Math.floor(x)), f = x - i;
  const c = STOPS[i].map((a, k) => Math.round(a + (STOPS[i + 1][k] - a) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

// First and last day of "YYYY-MM" for time_from / time_to.
export function monthRange(ym) {
  const m = /^(\d{4})-(\d{2})$/.exec(ym);
  if (!m) return null;
  const y = Number(m[1]), mo = Number(m[2]);
  if (mo < 1 || mo > 12) return null;
  const last = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  return [`${m[1]}-${m[2]}-01`, `${m[1]}-${m[2]}-${String(last).padStart(2, "0")}`];
}

export const months = (start, end) => {
  const out = [];
  if (!start || !end) return out;
  let y = Number(start.slice(0, 4)), m = Number(start.slice(5, 7));
  const ey = Number(end.slice(0, 4)), em = Number(end.slice(5, 7));
  while (y < ey || (y === ey && m <= em)) { out.push(`${y}-${String(m).padStart(2, "0")}`); if (++m > 12) { m = 1; y++; } }
  return out;
};

// A time as UTC text. The API may send an offset (+05:30); the dashboard always says UTC.
export function utc(text) {
  if (text === null || text === undefined) return "no value";
  const t = Date.parse(text);
  return Number.isFinite(t) ? new Date(t).toISOString().replace(".000Z", "Z") : String(text);
}

// The API shows times to the second but keeps finer ones, so one profile is asked for with a window of one second.
export function plusSeconds(text, n) {
  const t = Date.parse(text);
  return Number.isFinite(t) ? new Date(t + n * 1000).toISOString().replace(".000Z", "Z") : null;
}

// A series product lists columns (wind_speed_mean, wind_speed_min ...); the user chooses the base variable (wind_speed).
export function baseVariables(names) {
  const out = [];
  for (const n of names || []) {
    const b = n.replace(/_(mean|min|max|count)$/, "");
    if (!out.includes(b)) out.push(b);
  }
  return out;
}

// The columns of one base variable, as the product lists them (the API wants these names in `variable`).
export function columnsOf(names, base) {
  return ["mean", "min", "max", "count"].map((s) => `${base}_${s}`).filter((c) => (names || []).includes(c));
}

// Time axis: ticks on whole hours or days (UTC) that fall inside [min, max] (milliseconds), at most about n of them.
export function timeAxis(min, max, n = 6) {
  if (!isNum(min) || !isNum(max)) return null;
  const H = 3600000, D = 24 * H;
  const steps = [H, 3 * H, 6 * H, 12 * H, D, 2 * D, 5 * D, 7 * D, 10 * D, 14 * D, 30 * D, 60 * D, 90 * D, 180 * D, 365 * D];
  const step = steps.find((s) => (max - min) / s <= n) || 365 * D;
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max; t += step) ticks.push(t);
  return { min, max: max === min ? min + H : max, step, ticks };
}
