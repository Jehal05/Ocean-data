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
| Default passwords | Login page warns while seed passwords are in use, from a yes/no in `/health`. **Fixed.** |
| `/health` | Booleans and the version only; no account names, no host or database names. **Tested.** |
| Compose healthcheck | `api` already has one (`/health/live`); the Dockerfile now also copies the dashboard files. **Config checked only if the Docker CLI is present (SKIP otherwise); not run.** |
| Dead code and TODO sweep | No TODO/FIXME in the components or tests; ruff F/E9 clean. **Done.** |
| Third-party JS | None. **Tested by `test_dashboard_static.py`.** |
| Rate limit counts answered requests | Counted from `query_log` in the database, so it holds across replicas; refused requests are not logged and therefore not counted. **Noted, unchanged.** |

### Dependencies (versions the tests ran against)
Python 3.13; fastapi 0.165.0; starlette 1.7.0; uvicorn 0.54.0; psycopg2-binary 2.9.13; numpy 2.5.3; pandas 3.0.5; xarray 2026.9.0; netCDF4 1.7.5.1; zarr 3.4.2 (format 2); numcodecs 0.17.1; boto3 1.44.10; moto 5.2.6; gsw 3.6.23; PyYAML 6.0.3; pytest 9.1.1; ruff 0.17.6; Playwright 1.63.0 with Chrome for Testing 153.0.8010.12; Node 22.23.5 (tests only). The dashboard has **no** JavaScript dependency. The Dockerfile base image is `python:3.12-slim` (not run).

### Not verified
Docker build and run; SeaweedFS; PostGIS; browsers other than headless Chrome 153; screen readers (only role, label and focus checks); touch devices; large real datasets.
