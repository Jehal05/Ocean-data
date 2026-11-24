"use strict";
// Every piece of text from the server (comments, usernames, keys) goes into the page as a text node (never as markup).
// Nothing here builds markup from strings and there is no inline script.
const $ = (sel) => document.querySelector(sel);
let ME = null;

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const k of kids.flat()) n.append(k instanceof Node ? k : document.createTextNode(String(k ?? "")));
  return n;
}
function text(v) { return v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v); }

async function api(method, path, body) {
  const opts = { method, credentials: "same-origin", headers: { "X-Requested-With": "scats-ui" } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (r.status === 401 && path !== "/auth/login") { ME = null; location.hash = "#/login"; throw new Error("Please log in."); }
  if (!r.ok) {
    const d = data && data.detail !== undefined ? data.detail : r.statusText;
    throw new Error(typeof d === "string" ? d : JSON.stringify(d, null, 1));
  }
  return data;
}

function show(...nodes) { const m = $("#main"); m.replaceChildren(...nodes); m.focus(); }
function err(e) { return el("div", { class: "err", role: "alert" }, e.message || String(e)); }
function ok(msg) { return el("div", { class: "okmsg", role: "status" }, msg); }
function flagCell(f) { return el("span", { class: f === 4 ? "f4" : f === 3 ? "f3" : "" }, f); }
function field(label, input) { const id = "f" + Math.random().toString(36).slice(2); input.id = id; return el("div", {}, el("label", { for: id }, label), input); }
function link(href, label) { return el("a", { href }, label); }

function renderNav() {
  const nav = $("#nav");
  if (!ME) { nav.replaceChildren(); return; }
  nav.replaceChildren(link("#/", "Datasets"), el("span", { class: "note" }, `${ME.username} (${ME.role}) `),
    el("button", { class: "secondary", onclick: logout }, "Log out"));
}
async function logout() { try { await api("POST", "/auth/logout"); } catch (e) { /* already out */ } ME = null; renderNav(); location.hash = "#/login"; }

// ---------------------------------------------------------------- pages --
async function pageLogin() {
  renderNav();
  const msg = el("div");
  const user = el("input", { type: "text", autocomplete: "username", required: true });
  const pass = el("input", { type: "password", autocomplete: "current-password", required: true });
  const form = el("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", "/auth/login", { username: user.value, password: pass.value });
      ME = await api("GET", "/auth/me"); renderNav(); location.hash = "#/";
    } catch (e) { msg.replaceChildren(err(e)); }
  } }, field("Username", user), field("Password", pass), el("p", {}, el("button", { type: "submit" }, "Log in")));
  show(el("h2", {}, "Log in"), el("div", { id: "warning" }), msg, form);
  try {
    const info = await api("GET", "/auth/info");
    if (info.warning) $("#warning").replaceChildren(el("div", { class: "warn", role: "alert" }, info.warning));
  } catch (e) { /* not important for login */ }
}

async function pageList() {
  const status = el("select", {}, el("option", { value: "" }, "all"),
    ...["qc_done", "in_review", "approved", "rejected", "published"].map((s) => el("option", { value: s }, s)));
  const body = el("div");
  const load = async () => {
    try {
      const d = await api("GET", "/datasets" + (status.value ? `?status=${encodeURIComponent(status.value)}` : ""));
      body.replaceChildren(el("table", {}, el("caption", { class: "note" }, `${d.total} dataset(s)`),
        el("thead", {}, el("tr", {}, ...["Dataset", "Type", "Status", "Synthetic", "Flag 3", "Flag 4", "Missing", "By people", "Unreviewed 4"].map((h) => el("th", { scope: "col" }, h)))),
        el("tbody", {}, d.datasets.map((x) => el("tr", {}, el("td", {}, link(`#/d/${encodeURIComponent(x.dataset_key)}`, x.dataset_key)),
          el("td", {}, x.dataset_type), el("td", {}, x.status), el("td", {}, x.synthetic_status), el("td", {}, x.flags_probably_bad),
          el("td", {}, x.flags_bad), el("td", {}, x.flags_missing), el("td", {}, x.human_decisions), el("td", {}, x.unreviewed_flag4))))));
    } catch (e) { body.replaceChildren(err(e)); }
  };
  status.addEventListener("change", load);
  show(el("h2", {}, "Datasets"), field("Show status", status), body);
  load();
}

async function pageDataset(key) {
  const out = el("div");
  show(el("h2", {}, key), out);
  try {
    const d = await api("GET", `/datasets/${encodeURIComponent(key)}`);
    const msg = el("div");
    const reload = () => pageDataset(key);
    const kv = (k, v) => el("tr", {}, el("th", { scope: "row" }, k), el("td", {}, text(v)));
    const children = [
      el("p", {}, link(`#/d/${encodeURIComponent(key)}/flags`, "Flagged cells"), " | ", link(`#/d/${encodeURIComponent(key)}/audit`, "Audit trail")),
      el("table", {}, kv("Title", d.title), kv("Type", d.dataset_type), kv("Status", d.status), kv("Synthetic", d.synthetic_status),
        kv("Units assumed from mapping", d.units_assumed_from_mapping), kv("QC reference time", d.qc_reference_time),
        kv("QC config version", d.qc_config_version), kv("Observations", d.observations),
        kv("Current flags by value", d.current_flags_by_value), kv("Current flags by test", d.current_flags_by_test),
        kv("Decisions by people", d.human_decisions), kv("Unreviewed automatic flag 4", d.unreviewed_flag4),
        kv("Zarr store", d.store ? d.store.store_uri : "not published")),
      el("h3", {}, "Limits you should know about"), el("ul", {}, d.limits.map((l) => el("li", {}, l))),
      el("h3", {}, "Review decisions"),
      d.reviews.length ? el("table", {}, el("tbody", {}, d.reviews.map((r) => el("tr", {}, el("td", {}, r.decision), el("td", {}, r.reviewer),
        el("td", {}, r.decided_at), el("td", {}, r.comment))))) : el("p", { class: "note" }, "None yet."), msg];
    if (ME.role !== "viewer") children.push(actions(d, key, msg, reload));
    out.replaceChildren(...children);
  } catch (e) { out.replaceChildren(err(e)); }
}

function actions(d, key, msg, reload) {
  const box = el("div");
  if (d.status === "qc_done") {
    box.append(el("h3", {}, "Review"), el("button", { onclick: async () => {
      try { await api("POST", `/datasets/${encodeURIComponent(key)}/review/start`); reload(); } catch (e) { msg.replaceChildren(err(e)); } } }, "Start review"));
  }
  if (d.status === "in_review") {
    const decision = el("select", {}, ...["approved", "rejected", "needs_work"].map((x) => el("option", { value: x }, x)));
    const comment = el("textarea", { required: true });
    const accept = el("input", { type: "checkbox" });
    box.append(el("h3", {}, "Decision"), el("form", { onsubmit: async (ev) => {
      ev.preventDefault();
      try {
        const r = await api("POST", `/datasets/${encodeURIComponent(key)}/review`, { decision: decision.value, comment: comment.value, accept_unreviewed_flag4: accept.checked });
        msg.replaceChildren(ok(JSON.stringify(r, null, 1)));
        setTimeout(reload, 1500);
      } catch (e) { msg.replaceChildren(err(e)); }
    } }, field("Decision", decision), field("Comment (required)", comment),
    el("div", {}, el("label", {}, accept, " I accept the automatic flag-4 cells nobody has looked at (recorded in the audit trail)")),
    el("p", {}, el("button", { type: "submit" }, "Record decision"))));
  }
  if (d.status === "approved") {
    box.append(el("h3", {}, "Publish"), el("p", { class: "note" }, "Approved but not yet in the Zarr store."), el("button", { onclick: async () => {
      try { const r = await api("POST", `/datasets/${encodeURIComponent(key)}/publish`); msg.replaceChildren(ok(JSON.stringify(r, null, 1))); setTimeout(reload, 1500); }
      catch (e) { msg.replaceChildren(err(e)); } } }, "Publish to Zarr"));
  }
  return box;
}

async function pageFlags(key, query) {
  const params = new URLSearchParams(query || "");
  const out = el("div");
  const flagSel = el("select", {}, el("option", { value: "" }, "any"), ...[2, 3, 4, 9, 1].map((f) => el("option", { value: f, selected: params.get("flag") === String(f) }, f)));
  const varIn = el("input", { type: "text", value: params.get("variable") || "" });
  const revSel = el("select", {}, el("option", { value: "" }, "any"), el("option", { value: "false", selected: params.get("reviewed") === "false" }, "not yet looked at"), el("option", { value: "true", selected: params.get("reviewed") === "true" }, "decided by a person"));
  const go = () => { const q = new URLSearchParams(); if (flagSel.value) q.set("flag", flagSel.value); if (varIn.value) q.set("variable", varIn.value); if (revSel.value) q.set("reviewed", revSel.value); location.hash = `#/d/${encodeURIComponent(key)}/flags?${q}`; };
  show(el("h2", {}, `Flagged cells: ${key}`), link(`#/d/${encodeURIComponent(key)}`, "Back to the dataset"),
    el("form", { class: "row", onsubmit: (ev) => { ev.preventDefault(); go(); } }, field("Flag", flagSel), field("Variable", varIn), field("Review state", revSel), el("div", {}, el("button", { type: "submit" }, "Filter"))), out);
  try {
    const d = await api("GET", `/datasets/${encodeURIComponent(key)}/flags?${params}`);
    const msg = el("div");
    const picks = [];
    const canEdit = ME.role !== "viewer";
    const rows = d.flags.map((f) => {
      const cb = el("input", { type: "checkbox", "aria-label": `select ${f.variable} record ${f.source_record}` });
      picks.push([cb, f]);
      return el("tr", { class: f.set_by ? "human" : "" }, canEdit ? el("td", {}, cb) : null, el("td", {}, f.source_record), el("td", {}, f.variable), el("td", {}, f.level_index),
        el("td", {}, flagCell(f.flag)), el("td", {}, f.test_id), el("td", {}, f.set_by || "automatic"), el("td", {}, f.comment || ""),
        el("td", {}, f.set_by && f.automatic_flag !== null ? `automatic now: ${f.automatic_flag}` : ""),
        el("td", {}, link(`#/d/${encodeURIComponent(key)}/cell/${f.observation_id}?variable=${encodeURIComponent(f.variable)}&level_index=${f.level_index}`, "open")));
    });
    const cells = () => picks.filter(([cb]) => cb.checked).map(([, f]) => ({ observation_id: f.observation_id, variable: f.variable, level_index: f.level_index }));
    const newFlag = el("select", {}, ...[1, 2, 3, 4].map((x) => el("option", { value: x }, x)));
    const comment = el("textarea", {});
    const send = async (path, body) => {
      try { const r = await api("POST", `/datasets/${encodeURIComponent(key)}/${path}`, body); msg.replaceChildren(ok(`${r.n_cells} cell(s) ${r.action === "confirm" ? "confirmed" : "changed"}.`)); setTimeout(() => pageFlags(key, query), 800); }
      catch (e) { msg.replaceChildren(err(e)); }
    };
    out.replaceChildren(el("p", { class: "note" }, `${d.total} row(s), page ${d.page}`),
      el("table", {}, el("thead", {}, el("tr", {}, canEdit ? el("th", { scope: "col" }, "Pick") : null, ...["Record", "Variable", "Level", "Flag", "Test", "Set by", "Comment", "Automatic", ""].map((h) => el("th", { scope: "col" }, h)))), el("tbody", {}, rows)),
      msg, canEdit ? el("div", {}, el("h3", {}, "Change the picked cells"), field("New flag (1 good, 2 probably good, 3 probably bad, 4 bad)", newFlag), field("Reason (required)", comment),
        el("p", {}, el("button", { onclick: () => send("flags", { cells: cells(), flag: Number(newFlag.value), comment: comment.value }) }, "Change flag"), " ",
          el("button", { class: "secondary", onclick: () => send("flags/confirm", { cells: cells(), comment: comment.value }) }, "Confirm automatic flag"))) : null);
  } catch (e) { out.replaceChildren(err(e)); }
}

async function pageCell(key, obs, query) {
  const out = el("div");
  show(el("h2", {}, `Cell view: ${key}, observation ${obs}`), link(`#/d/${encodeURIComponent(key)}/flags`, "Back to flagged cells"), out);
  try {
    const d = await api("GET", `/datasets/${encodeURIComponent(key)}/cells/${encodeURIComponent(obs)}?${query || ""}`);
    const o = d.observation;
    const tables = d.series.map((s) => {
      const axis = s.depth_m ? ["Level", "Depth (m)"] : ["Record", "Time"];
      return el("div", {}, el("h3", {}, `${s.variable} (${s.units || ""})`), el("table", {}, el("thead", {}, el("tr", {}, ...axis.map((h) => el("th", { scope: "col" }, h)), ...["Value", "Flag in staging", "Effective flag"].map((h) => el("th", { scope: "col" }, h)))),
        el("tbody", {}, (s.level_index || s.record).map((i, j) => el("tr", { class: i === s.centre_record ? "human" : "" }, el("td", {}, i), el("td", {}, s.depth_m ? s.depth_m[j] : s.time[j]), el("td", {}, s.value[j]), el("td", {}, flagCell(s.staging_flag[j])), el("td", {}, flagCell(s.effective_flag[j])))))));
    });
    out.replaceChildren(el("p", {}, `Platform ${text(o.platform_id)}, time ${text(o.obs_time)}, position ${o.latitude}, ${o.longitude}`), ...tables,
      el("h3", {}, "Flags and their history"), el("table", {}, el("tbody", {}, d.flags.map((f) => el("tr", {}, el("td", {}, f.variable, " ", f.level_index), el("td", {}, flagCell(f.flag)), el("td", {}, f.test_id),
        el("td", {}, f.history.map((h) => `#${h.qc_flag_id} flag ${h.flag} by ${h.set_by || h.test_id}${h.comment ? ": " + h.comment : ""}`).join("\n")))))));
  } catch (e) { out.replaceChildren(err(e)); }
}

async function pageAudit(key) {
  const out = el("div");
  show(el("h2", {}, `Audit trail: ${key}`), link(`#/d/${encodeURIComponent(key)}`, "Back to the dataset"), out);
  try {
    const d = await api("GET", `/datasets/${encodeURIComponent(key)}/audit`);
    out.replaceChildren(el("p", { class: "note" }, `${d.total} event(s)`), el("table", {}, el("thead", {}, el("tr", {}, ...["#", "When", "Who", "Action", "Comment"].map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, d.events.map((e) => el("tr", {}, el("td", {}, e.audit_id), el("td", {}, e.occurred_at), el("td", {}, e.username), el("td", {}, e.action), el("td", {}, e.comment || ""))))));
  } catch (e) { out.replaceChildren(err(e)); }
}

// --------------------------------------------------------------- router --
async function route() {
  const [path, query] = (location.hash.slice(1) || "/").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (path === "/login") return pageLogin();
  if (!ME) {
    try { ME = await api("GET", "/auth/me"); } catch (e) { return; }
  }
  renderNav();
  if (parts.length === 0) return pageList();
  if (parts[0] === "d" && parts.length === 2) return pageDataset(parts[1]);
  if (parts[0] === "d" && parts[2] === "flags") return pageFlags(parts[1], query);
  if (parts[0] === "d" && parts[2] === "cell") return pageCell(parts[1], parts[3], query);
  if (parts[0] === "d" && parts[2] === "audit") return pageAudit(parts[1]);
  show(el("h2", {}, "Not found"), link("#/", "Datasets"));
}
window.addEventListener("hashchange", route);
route();
