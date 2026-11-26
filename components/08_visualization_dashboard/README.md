# 08 Visualisation dashboard

**Phase 5, built.** A browser dashboard on top of the Phase 4 access API and nothing else: it makes no database or store access of its own.

* Served by the access API itself at `/app` (same origin, so no CORS). Files: `dashboard/static/` (plain ES modules, SVG charts, **no third-party JavaScript, no build step, no CDN**).
* Views: Map, Profile, Series, SST, Explore/download. Every view shows data_version, product_version, qc_config_sha256 and the flags used, says when a page is truncated or a product is stale, and the footer says when the data is synthetic.
* `logic.js` holds the pure functions (axis scaling, flag labels, gap handling, longitude wrap, error text); `api.js` is the only file that talks to the network; `views.js` draws; `app.js` is the router and login.
* Every API string is inserted as text. Strict CSP: no inline script or style, no eval.
* Try it: `python3 tests/dash_stack.py` (see `docs/demo_script.md`). User guide: `docs/user_guide.md`. Design and "as built": `docs/phase5_design.md`.
* Tests: `node --test tests/js/logic.test.mjs`, `tests/unit/test_dashboard_static.py`, `tests/integration/test_dashboard_browser.py` (Playwright, headless Chrome), `tests/mutation_check_phase5.py`, `tests/check_phase5.py`.

Not in this component: SSO/LDAP, coastline or tiles, zoom and pan, vertical sections, T-S diagrams, mixed-layer depth, heat content, flow animation (listed for NPOL in `docs/phase5_design.md`, section 8).
