// Router, login and page frame. Everything shown comes from the access API; the session is an HttpOnly cookie (never readable here).
import { ApiError, get, post } from "./api.js";
import { clear, h } from "./svg.js";
import { exploreView, mapView, profileView, seriesView, sstView } from "./views.js";

const VIEWS = {
  map: ["Map", mapView], profile: ["Profile", profileView], series: ["Series", seriesView], sst: ["SST", sstView], explore: ["Explore and download", exploreView],
};
let ME = null;
let limitCache = null;
let datasetIndex = null;
let firstRender = true;

const ctx = {
  pageSize: 100,
  maxSeriesRows: 2000,
  async limits() {
    if (!limitCache) {
      const r = await get("/limits");
      limitCache = Object.fromEntries(r.limits.map((l) => [l.name, l.value]));
      ctx.pageSize = Math.min(200, limitCache.page_size_max);
    }
    return limitCache;
  },
  isSynthetic: (key) => (datasetIndex ? datasetIndex.get(key) === true : false),
  setSynthetic(flag) {
    document.getElementById("synthetic").textContent = flag ? "The data shown here is synthetic: it was made for testing and is not real ocean data." : "";
  },
};

const $ = (id) => document.getElementById(id);

function navBar() {
  const nav = clear($("nav"));
  if (!ME) return;
  for (const [k, [label]] of Object.entries(VIEWS)) nav.append(h("a", { href: `#/${k}`, "data-view": k }, label));
  const out = h("button", { type: "button", id: "logout" }, `Log out (${ME.username}, ${ME.role})`);
  out.addEventListener("click", async () => {
    try { await post("/auth/logout"); } catch { /* the session may already be gone */ }
    ME = null; limitCache = null; datasetIndex = null;
    navBar();
    location.hash = "#/login";
  });
  nav.append(out);
}

async function loadSession() {
  try {
    ME = await get("/auth/me");
    await ctx.limits();
    const all = await get("/datasets", { page_size: ctx.pageSize });
    datasetIndex = new Map(all.items.map((d) => [d.dataset_key, d.is_synthetic]));
  } catch { ME = null; }
}

async function loginView(root) {
  let warn = null;
  try {
    const r = await fetch("/health", { credentials: "same-origin" });
    if (r.ok && (await r.json()).default_passwords === true) warn = "This server still uses the published development passwords. Anyone who has read the repository can log in. Change them before any real use.";
  } catch { /* the page still works */ }
  const err = h("p", { class: "error", role: "alert" });
  const form = h("form", { class: "login", novalidate: true },
    h("label", { class: "field" }, h("span", {}, "Username"), h("input", { id: "username", name: "username", autocomplete: "username" })),
    h("label", { class: "field" }, h("span", {}, "Password"), h("input", { id: "password", name: "password", type: "password", autocomplete: "current-password" })),
    h("button", { type: "submit" }, "Log in"));
  root.append(warn ? h("p", { class: "warning", id: "default-passwords", role: "status" }, warn) : null, form, err);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    try {
      await post("/auth/login", { username: $("username").value, password: $("password").value });
      await loadSession();
      navBar();
      location.hash = "#/map";
    } catch (ex) { err.textContent = ex instanceof ApiError ? ex.message : String(ex); }
  });
}

async function route() {
  const m = /^#\/([a-z]*)(?:\?(.*))?$/.exec(location.hash) || [];
  let name = m[1] || "map";
  const params = new URLSearchParams(m[2] || "");
  const main = clear($("main"));
  ctx.setSynthetic(false);
  if (!ME) await loadSession();
  navBar();
  if (!ME && name !== "login") { location.hash = "#/login"; return; }
  if (ME && name === "login") { location.hash = "#/map"; return; }
  if (!ME) name = "login";
  const title = name === "login" ? "Log in" : VIEWS[name]?.[0] || "Not found";
  document.title = `${title} - SCATS`;
  main.append(h("h2", { tabindex: "-1", id: "heading" }, title));
  document.querySelectorAll("#nav a").forEach((a) => (a.getAttribute("data-view") === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  try {
    if (name === "login") await loginView(main);
    else if (VIEWS[name]) await VIEWS[name][1](main, ctx, params);
    else main.append(h("p", {}, "There is no such page."));
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { ME = null; navBar(); location.hash = "#/login"; return; }
    main.append(h("p", { class: "error", role: "alert" }, e.message || String(e)));
  }
  if (!firstRender) $("heading")?.focus();      // after a route change the heading gets focus; on the first load the skip link stays first in the tab order
  firstRender = false;
}

document.addEventListener("scats-unauthorised", () => { ME = null; limitCache = null; datasetIndex = null; navBar(); location.hash = "#/login"; });
window.addEventListener("hashchange", route);
route();
