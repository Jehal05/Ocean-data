PHASE 5 — Visualisation dashboard, hardening, demo (component 08)
Tender GEM/2026/B/8266583 (SCATS). Repo: Jehal05/Ocean-data, working copy /home/claude/ocean-data.
Start from branch phase-4-products-access (commit 4f986ce, already pushed).

AIM
A browser dashboard for the published data, built ONLY on the Phase 4 access API (component 07). It shows:
 1. Map: dataset/platform footprints and positions, with the bbox/time/depth filters the API already has.
 2. Profile view: one Argo/CTD profile (temperature, salinity, sound velocity against depth), flags visible.
 3. Time-series view: buoy series (hourly/daily products) with min/max band and count.
 4. SST view: a monthly mean grid as a map layer, with a time selector.
 5. Explore/download: filter, preview the first page, and download CSV/NetCDF through the API download route.
Every view shows data_version, product_version, qc_config_sha256 and the flags used, and says plainly when a page is truncated or a product is stale.
Then hardening and a demo script (see below).

OUT OF SCOPE (stop and say so if one looks necessary)
SSO/LDAP, propagation modelling, sediment DB, Kerchunk/VirtualiZarr, T-S diagrams, vertical sections, MLD, heat content, new derived products, QC threshold changes, changes to the Phase 3 review rules, changes to API semantics. Do not edit migrations 001-007. If the UI needs an API change, make it additive, list it, and add tests for it.

BRANCH AND COMMIT RULES
- Branch phase-5-dashboard from phase-4-products-access.
- Make exactly ONE new local commit at the end. Do NOT push, and do not merge anything.
- The message ends with these lines, and includes the qc_config.yaml sha256 (recompute it; expected unchanged: 225bc3afe0a0ff0beddd693b22c7b23c2d4994c3bc1f3ebf2ba5268a1c9130f3):
  Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0191YJPKXnnongEhrL2H94rc
- Save this prompt as docs/phase5_prompt.md and write docs/phase5_design.md BEFORE the code (choices table, views, API calls per view, security, test plan). Fill in an "as built" section at the end.

DESIGN RULES
- First look at what component 04 (review UI) already uses for front end, auth and CSRF; reuse its approach and libs/scats_auth. Do not invent a second login.
- Open-source only, pinned versions, no CDN: every JS/CSS file is served from the repo's own files (offline demo must work). Architecture doc suggests MapLibre plus Plotly or ECharts: use them if they can be vendored; otherwise say what you used and why. No map tiles from the internet: base layer is a local or drawn background (coastline from data already in the repo, or a plain graticule). Say so.
- The UI calls only the access API. No direct DB, no direct S3.
- Token handling: HttpOnly cookie session with the CSRF header the API already requires; never put the token in a URL, localStorage or log.
- Strict CSP (no inline script, no eval), nosniff, no-store on API answers. Every value from the API is inserted as text (no innerHTML with data). Test with hostile platform ids and dataset titles (the Phase 4 test data already has "=cmd|' /C calc'!A0" and similar).
- The UI shows what the API returns and does not recompute or smooth data. Missing values show as gaps, never as zero. Flags use shapes or labels as well as colour (colour-blind safe).
- Large answers: the UI pages through the API and never asks for more than the user's limits; it shows the 413/422/429/504 text from the API instead of failing silently.
- Accessibility basics: keyboard reachable controls, labels, text alternative for each chart (a data table toggle), contrast.

HARDENING (part of this phase)
- A security header and CORS review of the API; one finding list with fixed / not fixed.
- Rate-limit and login-failure behaviour re-tested through the UI path.
- Dev-password warning visible on the login page while the seed passwords are in use.
- /health stays booleans-only. Add a lightweight container healthcheck in compose (config only; not run).
- Remove dead code and TODOs you find; run a dependency list with versions in docs.

TESTS AND CHECKS REQUIRED
- Unit tests for any pure JS logic (axis scaling, flag labelling, bbox wrap at 180 degrees, gap handling) with hand-computed values. Use the simplest runner that works offline (node's built-in test runner or similar); if Node is not available, say so and write the logic so it can be tested in Python instead.
- Integration tests: start the real API on the test database and moto, load the pages in a headless browser (the built-in browser or Chrome if available; else SKIP and say so), and check: login, map shows the right number of footprints for the fixture, profile plot has the hand-computed points, series band, SST layer, truncated-page notice, stale notice, 403/413/429 messages shown, hostile strings appear as text and run nothing, CSP has no violations in the console, download button gives the server-made file name.
- tests/mutation_check_phase5.py: switch ONE safeguard off at a time, require a named test to fail, restore the file with a sha256 assertion. At least: text-only insertion (innerHTML), CSRF header, CSP, truncation notice, stale notice, gap handling (missing -> zero), flag labels, bbox wrap.
- tests/check_phase5.py with PASS/FAIL/SKIP evidence. It runs the new suites, the Phase 5 mutation check, tests/check_phase4.py (which includes Phase 1-3), and ruff --select F,E9. It fails if a vendored file is missing or changed, a CDN URL appears in any served file, the qc_config sha256 changed, or migrations 001-007 changed.
- No vacuous tests (no "or True", no assertions that cannot fail).
- Screenshots of each view from the fixture data go in docs/screens/ (only if a browser is available; otherwise SKIP).

DEMO
- docs/demo_script.md: a 10-minute walkthrough for the NPOL demo: which data, which clicks, what to say, what NOT to claim. A script that loads the fixture (synthetic, declared as such) into a clean stack with one command. Every number on screen must be traceable to the fixture.
- Say on the page, in the footer, that the data is synthetic whenever the dataset is flagged synthetic.

DOCS REQUIRED
README Phase 5 section (how to run, how to demo, what is not verified); docs/architecture.md update; component 08 README replaced; docs/phase5_design.md "as built"; a short user guide (docs/user_guide.md) for the five views.

HONESTY RULES
- Name the stand-ins (PostgreSQL 16 without PostGIS, moto). Do not claim SeaweedFS, PostGIS or Docker behaviour. Docker is not built or run; run `docker compose config -q`.
- Do not claim performance or browser coverage you did not measure. State which browsers were tested.
- Anything unverifiable is SKIP, not PASS.
- No peeking at answer_key.csv. No QC threshold tuning. Never delete from raw or standardised.
- Ask at most 3 short questions, only if truly blocked.

FINAL REPORT (items 1-7)
1. What was built (files, grouped by view/component).
2. Views table: view, API calls it makes, what it shows, hand-checked fixture value.
3. Hardening findings: fixed / not fixed.
4. Verification table PASS/FAIL/SKIP with evidence, with rows: login and session, map, profile, series, SST, download, truncation and stale notices, error messages, hostile strings as text, CSP, CSRF, offline (no CDN), accessibility basics, lint, Phase 1-4 regression, mutation check, Docker.
5. Existing tests or code changed and why, with before/after test counts.
6. Not verified / known limits.
7. The single commit sha, the qc_config sha256, and confirmation that nothing was pushed.