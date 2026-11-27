// Router, login and page frame. Everything shown comes from the access API; the session is an HttpOnly cookie (never readable here).
import { ApiError, get, post } from "./api.js";
import { clear, h, icon } from "./svg.js";
import { themeIsDark, themeNext } from "./logic.js";
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

const THEME_KEY = "scats-theme";
const THEME_LABEL = { system: "Theme: follow system", light: "Theme: light", dark: "Theme: dark" };
let themeChoice = "system";
try { const v = localStorage.getItem(THEME_KEY); if (v === "light" || v === "dark") themeChoice = v; } catch { /* storage may be blocked; the page still works */ }
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

function applyTheme() {
  document.documentElement.classList.toggle("dark", themeIsDark(themeChoice, systemDark.matches));
  const b = document.getElementById("theme");
  if (b) {
    b.replaceChildren(icon(themeChoice === "system" ? "auto" : themeChoice === "dark" ? "moon" : "sun"), h("span", {}, THEME_LABEL[themeChoice]));
    b.setAttribute("data-theme", themeChoice);
  }
}
systemDark.addEventListener("change", applyTheme);

function navBar() {
  const nav = clear($("nav"));
  const tools = clear($("tools"));
  document.body.classList.toggle("anon", !ME);
  const theme = h("button", { type: "button", id: "theme", class: "themebtn" });
  theme.addEventListener("click", () => {
    themeChoice = themeNext(themeChoice);
    try { localStorage.setItem(THEME_KEY, themeChoice); } catch { /* not stored; the choice still applies until the page is closed */ }
    applyTheme();
  });
  tools.append(theme);
  applyTheme();
  if (!ME) return;
  for (const [k, [label]] of Object.entries(VIEWS)) nav.append(h("a", { href: `#/${k}`, "data-view": k, title: label }, icon(k), h("span", { class: "navlabel" }, label.split(" ")[0])));
  const out = h("button", { type: "button", id: "logout" }, "Sign out");
  out.addEventListener("click", async () => {
    try { await post("/auth/logout"); } catch { /* the session may already be gone */ }
    ME = null; limitCache = null; datasetIndex = null;
    navBar();
    location.hash = "#/login";
  });
  tools.prepend(h("span", { class: "who" }, `${ME.username} \u00b7 ${ME.role}`));
  tools.append(out);
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
