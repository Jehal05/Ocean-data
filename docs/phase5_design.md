# Phase 5 design: dashboard, hardening, demo (component 08)

Written before the code. Section 9 "As built" is filled in at the end.
Scope: the browser dashboard on the Phase 4 access API, hardening, and the demo script. Not in scope: SSO/LDAP, propagation modelling, sediment DB, Kerchunk/VirtualiZarr, T-S diagrams, vertical sections, mixed-layer depth, heat content, new derived products, QC thresholds, Phase 3 review rules, changes to API semantics, migrations.

## 1. Choices

| Question | Choice | Why |
|---|---|---|
| Where is the UI served from? | The access API itself: `GET /app` and `GET /app/{name}` (public, fixed file list) | Same origin, so the HttpOnly cookie and CSRF header work and no CORS is needed. Additive: two new policy rows. |
| Front-end stack | Plain ES modules, no framework, no build step, **no third-party JavaScript at all**. Charts and the map are SVG built with `createElementNS`. | Offline demo, strict CSP (no inline script/style, no eval), nothing to vendor or audit. MapLibre needs a tile or style source and blob workers; Plotly is about 3.5 MB. Cost: fewer chart features (no zoom, no pan). Stated as a limit. |
| Base map | A drawn graticule (every 5 degrees) in an equirectangular projection. No coastline: the repo has no coastline data and no tiles are fetched from the internet. | Honest and offline. The map shows footprints against lat/lon lines only. |
| Auth | `POST /auth/login` sets the HttpOnly session cookie (the same code and accounts as the review UI, `libs/scats_auth`). Unsafe requests send `X-Requested-With: scats-ui`. The token is never put in a URL, localStorage, or a log. | Reuses the Phase 3 approach; no second login. |
| Every value from the API | Inserted with `textContent` / `setAttribute` on SVG, never `innerHTML`. | Hostile platform ids and titles (`=cmd|' /C calc'!A0`, `<img onerror>`) show as text. |
| Pure logic | `static/logic.js` (axis scaling, flag labels, gap handling, longitude wrap, URL building, error text). Tested with the Node test runner. | Hand-computed unit tests without a browser. |
| Browser tests | Playwright (Python) with the installed Chrome, against a real API on the stand-in database and moto (`tests/dash_stack.py`). | Real code path end to end. SKIP when no browser is available. |

## 2. Views (hash routes) and the API calls they make

| View | API calls | Shows |
|---|---|---|
| Login `#/login` | `GET /health` (flag `default_passwords`), `POST /auth/login` | Warning while seed passwords are in use. |
| Map `#/map` | `GET /datasets?...` (filters: q, platform_type, variable, bbox, time_from, time_to) | One rectangle (or point) per dataset footprint on a graticule; longitude extents that cross 180 are drawn in two parts; a table below; each dataset links to the other views. |
| Profile `#/profile` | `GET /datasets?dataset_type=profile`; `GET /datasets/{key}/subset?depth_max=6` (list of profiles); `GET /datasets/{key}/subset?platform=..&time_from=..&time_to=..&quality=..` | Variables against depth (depth axis downwards); flags as marker shapes plus labels; `quality=all` shows flagged values; a data-table toggle. |
| Series `#/series` | `GET /products?kind=series_hourly\|series_daily`; `GET /products/{key}/subset?variable=..&time_from=..&time_to=..` | Mean line with a min-max band and the count; hourly or daily; gaps are gaps; a data-table toggle. |
| SST `#/sst` | `GET /products?kind=sst_monthly_mean`; `GET /products/{key}/subset?bbox=..&time_from=..&time_to=..` | Monthly mean grid as coloured cells on the graticule, with a month selector and a colour bar. Grids are fetched for a bbox only (the API limit refuses more and the 413 text is shown). |
| Explore `#/explore` | `GET /datasets` or `/products`; `GET .../subset` (first page); `GET .../download?format=csv\|netcdf`; `GET /limits` | Filter, preview the first page, truncated-page notice, download with the server-made file name. |

Every view shows data_version, product_version, qc_config_sha256, the flags used, and the notices "truncated" and "stale". When a dataset is `is_synthetic` the footer says the data is synthetic.

## 3. Rules the UI follows

* The UI shows what the API returns. It does not smooth, interpolate, or fill. A missing value (`null`) is a gap in the line, a blank cell, or "no value" in the table; never a zero.
* The UI never asks for more than the limits: page sizes come from `GET /limits`; if the API answers 413/422/429/504 the text is shown as is, not hidden.
* Flags are never shown by colour alone: marker shape and a text label in the table (1 good, 2 probably good, 3 probably bad, 4 bad, 9 missing, 0 not checked).
* Keyboard: every control is a native button, link, input, or select; skip link; focus moves to the page heading on a route change; each chart has a table alternative.

## 4. Additive API changes (all tested)

1. `GET /app`, `GET /app/{name}` public, fixed list of files, CSP for pages `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`.
2. `GET /` redirects to `/app`.
3. `GET /health` gains one boolean, `default_passwords` (true / false / null if unknown). No names, no counts.
4. Extra response headers on every answer: `X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy: same-origin`, `Permissions-Policy` (everything off).

## 5. Hardening list (result in section 9)

Security header and CORS review; rate-limit and login-lock behaviour through the UI; default-password warning; compose healthcheck (config only); dead code and TODO sweep; dependency list with versions.

## 6. Security

No inline script or style anywhere; no `eval`, no `new Function`; no third-party origin; `connect-src 'self'`; the page is `no-store`; hostile strings tested in the UI; cookie `HttpOnly`, `SameSite=Strict`.

## 7. Test plan

* Node unit tests (`tests/js/logic.test.mjs`): axis scaling, flag labels, gap segments, longitude wrap and bbox, URL building, error text, safe file name display, colour scale. Hand-computed values.
* Python test of the static files (`tests/unit/test_dashboard_static.py`): no CDN or http(s) URL, no `innerHTML`, no inline script or style, no `eval`, vendored-file list (none).
* Browser integration (`tests/integration/test_dashboard_browser.py`): login, map footprint count, profile points, series band, SST cells, truncated notice, stale notice, 401/403/413/429 texts, hostile strings as text, no CSP violation, download file name, CSRF header on logout, keyboard focus, table alternative.
* `tests/mutation_check_phase5.py`: one safeguard off at a time.
* `tests/check_phase5.py`: runs everything and `check_phase4.py`.

## 8. Open points for NPOL

Coastline and bathymetry contours on the map (needs a licence-clear dataset); zoom and pan; vertical sections and T-S diagrams; which languages; browsers to support.

## 9. As built

**Built as designed**, with these corrections found while testing (each has a test):

* Profile times are returned to the second, so a single profile is fetched with a one-second window, not an exact instant.
* Products accept real column names (`wind_speed_mean`), not the base variable; the UI maps one to the other (`baseVariables`, `columnsOf`).
* Products refuse the `quality` parameter (422): the Explore view leaves it out for a product.
* The catalogue returns timestamps with the server offset (+05:30); the UI converts them to UTC.
* A late answer to an older request could overwrite a newer one (shown by the stale-product test failing): every view now draws only the newest request (`test_an_older_answer_arriving_late_never_overwrites_the_newer_one`).
* A 401 inside a view was swallowed: it now sends the user to the login page.
* The SST view says when it draws only part of the cells (it follows pages up to 2000 rows).

### Additive API changes (all tested in `tests/integration/test_access_api.py` and the browser tests)
`GET /app`, `GET /app/{name}` (public, fixed file list), `GET /` redirect, `default_passwords` boolean in `/health`, headers `X-Frame-Options`, `Cross-Origin-Resource-Policy`, `Permissions-Policy`. Three public rows were added to ROLE_MATRIX. Nothing else in the API changed. Migrations 001-007 are untouched.

### Hardening findings

| Item | Result |
|---|---|
| CORS | None configured and none needed (same origin). Cross-origin pages cannot read answers. **Fixed/confirmed.** |
| Response headers | `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, API CSP `default-src 'none'`, plus the three new ones. Page CSP is strict. **Fixed (added three).** |
| HSTS | Not set. TLS is not terminated by this service; set it at the reverse proxy. **Not fixed.** |
| Cookie `Secure` flag | Off by default (`AUTH_COOKIE_SECURE=false`) so a plain-HTTP demo works. Must be `true` behind TLS. **Not fixed; documented.** |
| Rate limit | Per user per minute from `api_limit`; the UI shows the 429 text. **Tested through the UI.** |
| Login lock | 5 failures per user / 20 per IP in 15 minutes; the UI says so. **Tested through the UI.** |
| Default passwords | Login page warns while seed passwords are in use, from a yes/no in `/health`. **Changed after review (trade-off):** a public yes/no tells an attacker that the seed passwords work, so `default_passwords` is now `true`/`false` only to a caller whose TCP peer is loopback (read from the connection, never from `X-Forwarded-For`/`Forwarded`), and `null` to everyone else; the answer is cached 60 s (`DEFAULT_PASSWORDS_CACHE_S`) so `/health` makes at most one database call a minute for it, and a failed lookup is not cached. Consequence: the warning is shown only when the dashboard is opened on the server itself. A request carrying any forwarding header (`X-Forwarded-*`, `Forwarded`, `X-Real-IP`) is treated as relayed and also gets `null`, so a reverse proxy on the same host does not make strangers look like the console (a proxy that adds no such header cannot be told apart; keep the proxy's default forwarding headers on). |
| `/health` | Booleans and the version only; no account names, no host or database names. **Tested.** |
| Compose healthcheck | `api` already has one (`/health/live`); the Dockerfile now also copies the dashboard files. **Config checked only if the Docker CLI is present (SKIP otherwise); not run.** |
| Dead code and TODO sweep | No TODO/FIXME in the components or tests; ruff F/E9 clean. **Done.** |
| Third-party JS | None. **Tested by `test_dashboard_static.py`.** |
| Dead code | Review found `overlaps` (never used by the app; deleted with its test), `normLon` and `FLAGS` (used only inside `logic.js`; no longer exported) and `axisFor` in `views.js` (deleted). `test_dashboard_static.py` now fails on any export of `logic.js`/`svg.js` that no other app file imports and on any top-level function or constant that nobody uses. **Fixed.** |
| Accessibility | WCAG contrast is computed from `style.css` (`test_dashboard_contrast.py`): it found the keyboard focus outline at 2.62:1 (now `#b35c00`, 4.60:1) and a min-max band with no outline at 1.38:1 against the page (the band now has a `#3f7aa0` outline, 4.54:1). axe-core 4.14.1 (installed with `npm install --prefix tests/tools`, never served) found links in the map table under the 24 px target size (WCAG 2.2, 2.5.8); fixed in `style.css`. No screen reader was used. **Fixed.** |
| Rate limit counts answered requests | Counted from `query_log` in the database, so it holds across replicas; refused requests are not logged and therefore not counted. **Noted, unchanged.** |

### Review fixes (commit after 6a3426e)
* `/health` default-password answer: loopback only, cached, failure not cached (above).
* Dead code removed; checked by test (above).
* SST and series 2000-row caps are now tested (notice text with exact N and M, cells or bars drawn equal N, number of requests equals the pages needed to reach the cap) in `test_sst_stops_at_the_row_cap...` and `test_series_stops_at_the_row_cap...`.
* A grid that crosses 180 degrees (`antimeridian` in the `--tiny` stack, catalogue edges 178 and -178) and a box that crosses 180 are each drawn in two pieces, tested in the browser.
* The test helper `until()` retried every exception, which hid page errors; it now retries only "page was replaced" errors and fails at once on any other, with the message.
* Making `until()` strict exposed callers that relied on the swallowed error; they are now null-safe.

### Found during the fixes, then fixed (second review round)
* **API bbox filter and footprints that cross 180.** A dataset whose catalogue edges are west 178 and east -178 (min > max) was returned for `-180,0,180,20` but not for `179,0,180,20` or `-180,0,-178,20`, which overlap it. `service.py` now cuts the box into plain pieces and treats a crossing footprint as the two plain pieces `[lon_min,180]` and `[-180,lon_max]`. This is a correctness fix to Phase 4 behaviour and changes results only for footprints that cross 180; plain footprints answer exactly as before (tested: `test_a_footprint_that_crosses_180...`, `test_a_plain_footprint_is_found_by_a_box_that_crosses_180...`).
* **SST colour scale.** Measured against the page: the five viridis stops are 14.83, 7.39, 3.72, 2.04 and 1.23 to 1; the last two are under 3. The colour is unchanged (the Node tests pin it). Each cell now has a 4.5:1 outline (`#5d6e7a`) and the colour bar a frame, and the values are also in the table under the chart. Tested in `test_dashboard_contrast.py` and the browser tests.
* **`/health` behind a proxy on the same host:** see the hardening table.

### Still not fixed or not verified
* No screen reader was used (not possible here). Only role, label, focus, contrast and axe-core checks were run.
* Docker was not built or run; PostgreSQL 16 without PostGIS and moto stand in for the real services; only headless Chrome for Testing 153 was used.
* The map draws nothing (and so no box) when a filter matches no dataset; it says "0 datasets shown".

### Dependencies (versions the tests ran against)
Python 3.13; fastapi 0.165.0; starlette 1.7.0; uvicorn 0.54.0; psycopg2-binary 2.9.13; numpy 2.5.3; pandas 3.0.5; xarray 2026.9.0; netCDF4 1.7.5.1; zarr 3.4.2 (format 2); numcodecs 0.17.1; boto3 1.44.10; moto 5.2.6; gsw 3.6.23; PyYAML 6.0.3; pytest 9.1.1; ruff 0.17.6; Playwright 1.63.0 with Chrome for Testing 153.0.8010.12; Node 22.23.5 (tests only); axe-core 4.14.1 (tests only, `tests/tools/package.json`). The dashboard has **no** JavaScript dependency. The Dockerfile base image is `python:3.12-slim` (not run).

### Not verified
Docker build and run; SeaweedFS; PostGIS; browsers other than headless Chrome 153; screen readers (only role, label and focus checks); touch devices; large real datasets.


## 10. UI redesign: "Chart room"

Look and interaction only. The API, QC thresholds, migrations, the calls each view makes and every number a view shows are unchanged; every Phase 5 rule (versions and flags shown, truncated and stale notices, synthetic footer, plain-word errors with the status, gaps never zeros, flags by shape and label) still holds.

**Choices.** Plain ES modules and SVG, system font stacks only (serif for titles and numbers, sans for controls, tabular numerals), no library, no build, no web fonts. All colours are CSS custom properties on `:root`; `:root.dark` overrides them (a test fails if a colour token is missing from the dark block). The theme is a class on `<html>` set by script (never an inline style); the choice (system, light, dark) is kept in `localStorage` inside try/catch, so the page works when storage throws. Transitions are 120 ms on colour and opacity; animations stop under `prefers-reduced-motion`. Layout: top bar (product, user and role, theme, Sign out), left sidebar (icons plus labels; icons only below 1000 px; bottom bar below 760 px), filters in a collapsible left panel (closed by default on a phone), result on the right. Provenance is a slim caption strip; flags are chips (marker shape plus words) in the legend, tables, readouts and strip. A dataset's `flags_used` is a sentence in the API, so the strip shows the flags the chosen quality implies (good: 1, 2; all: all six).

**Contrast (computed from `style.css` by `tests/unit/test_dashboard_contrast.py`, both themes; text needs 4.5:1, marks and focus 3:1).** Before (white page): focus outline 4.72, band outline 4.66, cell outline and plot frame 5.28. After, light (warm page #f7f4ee) / dark (#0e1923): body text 14.55 / 15.16, muted text 6.48 / 8.57, links 5.73 / 8.39, white-on-teal buttons 6.29 / 8.92 (dark text on light teal), sidebar links 12.73 / 15.91, graticule labels on the ocean 5.59 to 6.21 / 7.69 to 8.52, band outline 4.38 / 8.43, plot frame 4.81 / 6.66, empty-cell outline 3.88 / 6.66, selected footprint and box outline on the ocean 3.72 / 10.02, focus outline 4.30 on the page, 3.10 on the navy bars, 4.72 on a panel / 10.07, 10.71, 9.07. SST viridis stops unchanged (measured 14.83, 7.39, 3.72, 2.04, 1.23 to 1 on white); the cell outline and colour-bar frame stay at 3:1 or more in both themes. A hover colour of the primary button was lowered in contrast by an opacity change; axe found it, so hover now uses a darker token instead.

**Checks.** Node tests 21 before, 30 now (pure logic added). Browser tests in headless Chrome: new tests for sidebar, strip, Copy buttons and fallback, chips, map card by mouse and keyboard, crosshair, brush, range inputs, hatched gap, month slider, Play, reduced motion, cell readout, explore steps and cards, skeleton, empty states, theme persistence and throwing storage, no sideways scroll at 360 px, no page error or CSP violation in either theme, axe (A/AA) on every view in both themes at 360 and 1280 px. Mutation check: one mutation per new safeguard (`tests/mutation_check_phase5.py`).

**Deliberate changes to existing tests.** The legend assertion (`svg.legend text` = 5 became `.legend .chip` = 6: the legend is now six chips, including "not checked"); the tab-order assertion (after the skip link come the top-bar buttons, then the sidebar); the series hint assertion (there is now a second hint, so it looks for the band hint by its text); the chart-role assertion (an arrow-key chart is a focusable group with a name, other charts stay images); the nav label (Explore). No assertion was weakened.

**Not done.** No coastline or tiles; no zoom or pan on the map; the series brush works by drag and the range sliders, not by touch gestures; no screen reader was run (axe and keyboard tests only); the checks ran in headless Chrome for Testing 153 only (no Firefox or Safari); the sticky bottom bar shows in the middle of the full-page 360 px screenshots because they are full-page captures; on a 360 px screen the series count bars merge into a block when every bin holds one value (the data, not a fault).
