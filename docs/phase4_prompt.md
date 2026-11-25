ROLE
You are continuing the SCATS proof of concept for DRDO/NPOL tender GEM/2026/B/8266583. Repo: https://github.com/Jehal05/Ocean-data (working copy /home/claude/ocean-data). Phases 1-3 are done. Phase 3 is committed locally as 1dc9a84 on branch phase-3-review-store (not pushed). Execute Phase 4 end to end. Do not stop to ask unless a rule below says to stop.

AIM
Phase 4 = derived products + data access API:
 (a) a products pipeline (component 06) that builds derived datasets ONLY from approved and published data in the standardised zone, and writes them to the derived zone as Zarr;
 (b) a data access API (component 07): discovery (catalogue search), subsetting (time / depth / bounding box / variable / platform), download (NetCDF and CSV, plus Zarr listing), with authentication and per-role limits;
 (c) lineage: every derived product records exactly which approved datasets, review_version, qc_config sha256 and code version it came from, and is rebuildable.

OUT OF SCOPE (do NOT build; if one looks necessary, stop and say so)
Dashboard / map UI (Phase 5), SSO/LDAP, propagation modelling, sediment DB, Kerchunk/VirtualiZarr, any change to QC thresholds, any change to the review workflow rules from Phase 3. Do NOT start Phase 5.

FIRST (before any code)
1. Read docs/architecture.md, docs/data_model.md (section 7 layout), docs/phase3_design.md, components/06_products_pipeline/README.md and components/07_data_access_api/README.md, and the existing 07 FastAPI skeleton.
2. Write docs/phase4_design.md BEFORE the code: choices table, derived-product list with exact definitions and units, zone layout, API route list with the role matrix, query limits, lineage record format, failure behaviour, open points for NPOL. Keep it true at the end ("as built").
3. Save this prompt as docs/phase4_prompt.md.

BRANCH AND COMMIT RULES
- Create branch phase-4-products-access from phase-3-review-store. Do NOT push. Make exactly ONE new local commit at the end.
- Commit message ends with:
  Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0191YJPKXnnongEhrL2H94rc
- Put the sha256 of qc_config.yaml in the message (recompute with sha256sum; expected unchanged: 225bc3afe0a0ff0beddd693b22c7b23c2d4994c3bc1f3ebf2ba5268a1c9130f3).

DERIVED PRODUCTS (keep it small and exact; each needs a written definition, units, a fill-value rule and a test against a hand-computed value)
 1. Binned profile product: Argo and CTD profiles to fixed depth levels (levels in config, not hard-coded), using only effective flag 1 or 2 values (flag 3, 4, 9 excluded). Record n_obs per bin.
 2. Time-binned series: buoy variables to hourly and daily mean/min/max/count, good flags only. Define what happens with partial bins (minimum count in config; below it the value is missing, not interpolated).
 3. Gridded climatology / composite: SST grid to a monthly mean on the source grid (no regridding in this phase), good flags only.
 4. Bathymetry tiles or subset index: only what the API needs to subset the grid fast. Do not invent a new product if the standardised Zarr is enough; say so.
 Rules: no interpolation across gaps, no extrapolation, no smoothing. Every product carries units, a description, the flags used, and the minimum-count rule as attributes.

RULES TO KEEP
- Input = only datasets with status published, read through scats_store.read_store, and only the effective flags at the approved review_version. Never read staging or raw to build a product. Never read an unapproved, rejected or in_review dataset (test it).
- Do not break Phases 1-3. Say which existing tests changed and why. Phase 3 migrations 001-006 are not edited; DB changes are additive only (db/init/007_* and up).
- No peeking at answer_key.csv. No tuning of QC thresholds. Do not re-score the blind set.
- Never delete from raw or standardised. The derived zone is rebuildable, so a rebuild may replace a product, but only by the same safe write as Phase 3 (temp prefix, verify, commit, manifest last) and the old version stays recorded in the catalogue (superseded, not erased).
- A derived product is deterministic: same inputs + same code version = same content_sha256. Test it (build twice, compare).
- If a dataset is later re-reviewed or re-published, dependent products become stale: the catalogue marks them stale (never silently serving old numbers as current), and the API shows the flag.

DATA ACCESS API: SECURITY (same standard as Phase 3)
- Authentication: reuse libs/scats_auth (Bearer token or HttpOnly SameSite=Strict cookie). Roles: viewer = read/discover/download within limits, reviewer same, admin additionally manages API limits. Anonymous access is refused. Server-side authorisation from one policy table (method, path) -> role; unlisted route = 403 (fail closed); a test asserts routes == policy rows. Docs and openapi off unless a setting turns them on.
- DB role: a separate least-privilege role scats_access: SELECT on catalogue and the derived-product tables only; INSERT on audit_log and a download/query log; NO access to app_user, auth_session, login_attempt, qc_flag writes, review writes. Prove it with parametrised denial tests. The products pipeline gets its own role scats_products with INSERT/UPDATE only on the derived catalogue tables.
- Every query and every download writes an audit/query-log row (who, what filter, how many rows or bytes, product version). The log row is written in the same transaction as the catalogue read it describes, or the download is refused.
- Injection: every filter is validated and parameterised; no filter text reaches SQL, a file path, an S3 key or a shell. Test with hostile values (quotes, ../, %00, huge numbers, NaN, inverted ranges, longitude wrap at 180, lat outside +-90).
- Limits (enforced and documented in code, API and docs): max rows per page, max rows or bytes per download, max time span per request, max bbox area for unauthenticated-style heavy requests, request timeout, per-user rate limit. Over-limit = 413 or 422 with a clear message, never a silent truncation. A truncated page must say it is truncated.
- Output safety: filenames are generated by the server (no user text); CSV cells beginning with = + - @ are neutralised; JSON only contains plain values (NaN to null); Content-Disposition and nosniff set; no-store on authenticated responses.
- Subsetting must read only the chunks it needs from Zarr (lazy). Test that a small window does not download the whole store (count the objects fetched with moto). If lazy reads cannot be done safely say so and state the limit.
- Responses always carry data_version, product_version, qc_config_sha256 and the flags used, so a user can cite exactly what they got.

TESTS (all must pass; use the existing stand-ins: local PostgreSQL 16 without PostGIS, moto; Docker daemon is NOT available, so say "not built / not run")
- Unit: each product against a hand-computed value; determinism (build twice = same sha256); partial-bin rule; flags 3/4/9 excluded; no interpolation.
- Integration: only published data is used; stale marking after re-publish; rebuild keeps the old version recorded; role matrix (every role x every route); least-privilege denials for scats_access and scats_products; query-log row written atomically (inject a failure and show the download is refused); limits (each one hit once); hostile filters; CSV injection; lazy subset reads; NetCDF/CSV download round trip equals the Zarr source for a sample; longitude wrap; empty result is a valid empty answer, not an error.
- Mutation check tests/mutation_check_phase4.py: switch ONE safeguard off at a time and require a named test to fail, then restore the file (assert sha256 is restored). At least: authorisation check, fail-closed on unlisted route, published-only input filter, good-flag filter, stale marking, query-log atomicity, a limit, CSV neutralisation, hostile-filter validation, lazy-read guard, determinism. Report which test failed for each.
- tests/check_phase4.py: PASS / FAIL / SKIP with evidence. It runs the new suites, the mutation check, tests/check_phase3.py (Phase 1-3 regression), and ruff --select F,E9. It fails if a role-matrix row is missing, a limits phrase disappears from code or docs, a mutation is not caught, a product is not deterministic, or the qc_config sha256 changed.
- Do NOT write tests that assert "or True" or merely "no exception". Each test needs a failing counter-example in the mutation check or a comment saying why it cannot have one.

DOCS
- README: Phase 4 section (how to build products, how to query with curl and a Bearer token, limits, what is not verified).
- docs/architecture.md: update the open items. components/06 and 07 READMEs: replace "not built". docs/data_model.md: derived-zone layout and the lineage record.
- docs/phase4_design.md "as built" section, checked against the code.

HONESTY
- Name the stand-ins (PostgreSQL without PostGIS, moto). Do not claim SeaweedFS or PostGIS behaviour. Do not claim a Docker image or compose service works unless it was run; otherwise write "not built / not run" and run `docker compose config -q`.
- A bounding-box search without PostGIS uses plain lat/lon comparisons: say so, and say what changes when PostGIS is present.
- If something cannot be verified here, mark it SKIP with the reason, not PASS.
- Ask at most 3 short questions, only if truly blocked. Otherwise decide, record the decision in docs/phase4_design.md, and continue.

FINAL REPORT (exactly these items)
1. What was built (files and one line each, grouped by component).
2. Derived products table: name, input, definition, units, partial-bin rule, hand-checked value.
3. API route table: method, path, minimum role, limits.
4. Verification table with PASS / FAIL / SKIP and evidence, with these rows: authentication, authorisation matrix, least-privilege roles, published-only input, good-flag filter, determinism, lineage record, stale marking, safe write of derived products, lazy subset read, limits documented and enforced, hostile filters, CSV injection, query-log atomicity, download round trip, lint, Phase 1-3 regression, mutation check.
5. Existing tests changed and why; before/after test counts.
6. Not verified / known limits (be specific).
7. The single commit sha, the qc_config.yaml sha256, and confirmation that nothing was pushed.