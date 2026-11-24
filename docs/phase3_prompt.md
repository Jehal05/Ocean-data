# Phase 3 prompt: expert review, accounts, audit trail, Zarr data store

(Format: ART = Aim, Rules, Tasks. Paste this whole file as one message.)

## AIM

Repo https://github.com/Jehal05/Ocean-data, working copy /home/claude/ocean-data. Phase 2 is committed locally (3687b78 on phase-2-parser-qc, not pushed). Create branch `phase-3-review-store` from it. This is Phase 3 of 5 of the SCATS proof of concept (DRDO/NPOL tender GEM/2026/B/8266583).

Phase 3 turns "datasets with automatic flags" into "datasets a named person has reviewed, approved and published to a Zarr store, with every decision on record". Four deliverables:

1. Accounts and roles that actually protect the API (replaces the shared INGEST_API_KEY for people).
2. The expert review workflow: list datasets, inspect flagged cells, change flags with a reason, approve or reject a dataset. Backend API plus a small web UI (components/04_expert_review_ui).
3. The audit trail wired in: every flag change and every status change writes to `audit_log` (table and hash chain already exist from Phase 1).
4. The three-zone Zarr store (components/05_data_store): approved data is written to the `standardised` bucket as Zarr, with the final flags, and the catalogue is updated. This replaces the NetCDF staging files for approved data.

**Out of scope. Do NOT build:** derived products (T-S, sections, MLD, OHC, sound speed, climatologies), the discovery/subsetting/download API (Phase 4), the dashboard with map and profile plots (Phase 5), real SSO/LDAP, propagation modelling, the sediment database. If one of these looks necessary, stop and say so in the report.

Success means a reviewer can log in, see a dataset with flagged cells, override a flag with a reason, approve the dataset, and the approved data appears in Zarr with the reviewer's decisions applied. The audit log can prove who did what, and a wrong person cannot do it.

## RULES

1. **Do not break Phase 1 or 2.** Existing tests keep passing. If a test encoded behaviour you must change, say which test and why in the report.
2. **Database changes are additive.** New files `db/init/006_*.sql` and up. Do not edit 001-005.
3. **No peeking.** QC code never reads `answer_key.csv`. Only `tests/score_qc.py` reads keys.
4. **No tuning to test data.** If you change any QC threshold or rule, give a written reason and source, add a section 8 entry in docs/phase2_design.md (or a new section in a phase3 design doc), a qc_config.yaml history entry and a version bump. This phase should not need to.
5. **Passwords and secrets.** No password in code, logs, responses or the audit log. Hash with bcrypt via `pgcrypto crypt()` (as seed users do) or `passlib`. Session tokens are random (>=32 bytes), stored hashed, expire, and can be revoked. Seed users from 003 stay development only: the README and the login page must say their passwords are known and unsafe, and a deployment check (script or startup warning) must flag them while the default passwords still work.
6. **Least privilege.** Create a separate DB role `scats_review` for the review service (new migration). It may read what it needs, insert into `qc_flag`, `review`, `audit_log`, update only `dataset.status`/`updated_at` through a guard trigger like 005's. It may NOT update or delete `qc_flag` or `audit_log`, and may NOT touch `app_user.password_hash` except through a dedicated function. Do not run any service as superuser.
7. **Server-side authorisation only.** The UI hiding a button is not security. Every endpoint checks role on the server. Roles: viewer (read), reviewer (viewer + change flags, approve/reject, comment), admin (reviewer + manage users, ingest, config). No role can approve a dataset it ingested itself if `REVIEW_FOUR_EYES=true` (default true); make this a documented setting with a test either way.
8. **A flag only moves by adding a record.** Never UPDATE or DELETE `qc_flag`. A human override is a new row with `set_by_user_id`, `test_id='expert_review'`, `comment` required (non-empty after trimming). A human override always supersedes automatic flags, including when QC is re-run later (add a test that a re-run does not undo an override).
9. **Audit is atomic with the change.** The flag/status change and its audit row commit in one transaction or neither does. Add a test that forces the audit insert to fail and checks the change did not persist. `audit_verify_chain()` must pass after the full workflow, and a test must show it fails when a row is tampered with (as a superuser).
10. **Zarr write is safe.** Write to a temporary prefix, verify (shape, dtype, checksum of data and flags, round-trip equality against the source), then rename/commit, then update the catalogue. A failure leaves the old state intact and says so. Never write unapproved data to `standardised`. Never delete from `raw`.
11. **Be honest.** Report what ran, what did not, and what is assumed. No Docker daemon here: say "not built / not run". Name stand-ins (stand-in PostgreSQL without PostGIS, moto for S3). Do not claim SeaweedFS compatibility from moto passing; list it under Not verified.
12. **Do NOT push.** One new local commit at the end on `phase-3-review-store`, ending with:
    Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_0191YJPKXnnongEhrL2H94rc
    Put the sha256 of qc_config.yaml in the message.
13. Ask at most 3 short questions, and only if you are truly blocked. Otherwise take the most reasonable reading, state it at the top of your report, and carry on.
14. Review your own work against the checklist in task 11 before reporting. Do not start Phase 4.

## TASKS

**1. Design note (first, short).** Write docs/phase3_design.md (about 2 pages): the review state machine, the role matrix, the audit event list, the Zarr layout, failure behaviour, and an "assumptions and limits" section. State open points for NPOL (confidentiality classes, their own login system, who may approve what). Do this before code, then keep it true.

**2. Accounts and sessions** (components/02_parser_api or a new shared `scats_auth` library; pick one and justify). Endpoints: `POST /auth/login` (username+password -> token), `POST /auth/logout`, `GET /auth/me`. Failed logins are rate limited per username and per IP (simple in-DB or in-memory counter is fine; document which) and written to the audit log without the password. Admin-only: create user, disable user, reset password, change role. Disabled users cannot log in and existing tokens stop working. Replace the shared key for human use; `/ingest` accepts an admin token OR the existing service key (document both).

**3. Review backend.** A new FastAPI service `review-api` (port 8002, localhost-bound in compose, own Dockerfile, runs as `scats_review`). Endpoints, all requiring a token:
- `GET /datasets?status=` list with counts of flags by severity and review state.
- `GET /datasets/{key}` metadata, QC summary (reuse the Phase 2 content including limits, synthetic_status, units_assumed_from_mapping, qc_reference_time).
- `GET /datasets/{key}/flags?variable=&flag=&test=&page=` current flags (use `qc_flag_current`), with the automatic history for each cell.
- `GET /datasets/{key}/cells/{observation_id}` data values with their flags and QC detail for context (neighbouring levels or times), so a reviewer can judge.
- `POST /datasets/{key}/flags` change one or many flags: body has cells, new flag (from the scheme), required comment. Validate the cell belongs to the dataset. Reject changing a flag to the value it already has. Cap batch size and say what the cap is. Write qc_flag and audit rows in one transaction.
- `POST /datasets/{key}/review` decision approved | rejected | needs_work with comment. Allowed transitions only over the existing `dataset.status` values (ingested, qc_running, qc_done, in_review, approved, rejected, published; document the state machine; test every illegal transition returns 409). Approving a dataset with unresolved flag-4 cells that no human has looked at must be refused unless the body sets `accept_unreviewed_flag4=true`, and that choice is recorded in the audit log. Decide and document what "reviewed" means.
- `GET /datasets/{key}/audit` the audit trail for that dataset (viewer sees only their allowed fields; no hashes needed in the response but offer `GET /audit/verify` for admin).

Limits to document in the API (like the Phase 2 LIMITS list): batch cap, page size, what review does not cover (no per-user locking, concurrent reviewers last-write-wins on the same cell but both rows are kept).

**4. Concurrency.** Two reviewers changing the same cell: both rows kept, latest wins, both audited. A dataset approved while another reviewer is mid-change must not produce an approved dataset whose final flags differ from what was approved: record a `review_version` (e.g. the max qc_flag_id for the dataset at decision time) and have the Zarr write use exactly that snapshot. Test it.

**5. Zarr data store** (components/05_data_store, library `scats_store`). On approval (or by an explicit admin `publish` call, choose one and document): read the staging dataset, apply the human overrides from `qc_flag_current` for the review snapshot, write Zarr at `<zone>/<platform_type>/<dataset_key>.zarr` per docs/data_model.md section 7, with `<variable>_qc` arrays, dataset attrs, the review decision, reviewer, review_version and qc config version. Chunking: choose and justify (profiles by n_profiles, time series by time, grids by lat/lon tiles); no chunk over about 50 MB. Compression: a lossless codec. Never lossy for data or flags. Keep attrs JSON-safe. Rejected datasets are never written to `standardised`. Update `dataset.status`, catalogue/storage path and checksums in the DB. Add a reader function and a round-trip test per data type (argo, ctd, buoy, sst grid, bathymetry grid). Pin the zarr and numcodecs versions and say which Zarr format version is written (v2 or v3) and why. Kerchunk/VirtualiZarr: do NOT build now; note it as deferred in the design doc with the reason.

**6. Staging retirement and consistency.** After a successful Zarr publish, the NetCDF staging file is kept (do not delete) but marked superseded in the catalogue. Extend `verify_flag_consistency` (or add a sibling) to compare the Zarr flags against `qc_flag_current` for the review snapshot. Resolve the open Phase 2 question: what happens to a cell that stops being flagged after a QC config change. Choose a rule, document it and test it. Suggested: automatic re-QC writes a new row with the new automatic result; the old row stays as history; human overrides are never superseded by automatic rows.

**7. Review UI** (components/04_expert_review_ui). Small, no build step needed if possible (plain HTML+JS served by the review-api, or a minimal React/Vite app if you justify it). Pages: login; dataset list; dataset detail with QC summary and limits; flagged cells table with filters; cell view showing the value and its neighbours (a simple table or tiny inline chart is enough; the real profile/section plots are Phase 5); change-flag form with required comment; approve/reject with comment; audit trail view. Show errors from the API plainly. No secrets in localStorage beyond the session token; use an httpOnly cookie or document why not. Escape all user text (comments, usernames) to avoid XSS and add a test with a script-tag comment. Keyboard accessible forms and visible labels. Do not build charts, maps or dashboards.

**8. Database migrations (additive).** 006 and up: role `scats_review` and grants; session table (hashed tokens, expiry, revoked_at); login-attempt tracking if kept in DB; `review` table additions only if needed (review_version, snapshot id) via ALTER ADD COLUMN; guard triggers for status transitions; a view for dataset review counts. Keep the 004/005 least-privilege style: column-level grants, a test that the role cannot do what it must not do (UPDATE/DELETE on qc_flag and audit_log, change password_hash, set a dataset to a status outside its allowed transitions).

**9. Compose and config.** Add `review-api` to docker-compose.yml (validate with `docker compose config`; do not claim it was built). `.env.example`: all new settings documented, no default secrets that work in production. Bind to localhost. The default for anonymous access is off.

**10. Tests.** Unit and integration (stand-in PostgreSQL, moto). Must include: login success/failure/rate limit/disabled/expired/revoked token; every role against every endpoint (a matrix test generated from one table so a new endpoint without a role entry fails); four-eyes on and off; illegal transitions; override beats re-run; atomic audit failure; concurrent change; approval snapshot equals Zarr content; Zarr failure leaves old state; XSS comment escaped; audit chain valid after the workflow and broken after tampering; SQL injection attempt in filters returns 4xx or empty, never leaks. Keep each test checking something specific; no test that passes if the feature is removed (do a mutation check on at least: authorisation, comment required, four-eyes, audit atomicity, snapshot use. Disable each and show a test fails).

**11. Acceptance script tests/check_phase3.py and self-review.** Runs everything and prints PASS/FAIL/SKIP with evidence, like check_phase2.py. It must also re-run check_phase2.py (Phase 1 and 2 regression) and the lint (`ruff check --select F,E9`). It must fail if: a role-matrix row is missing; a limits sentence disappears from the docs/API; the mutation checks do not fail when the feature is disabled; the Zarr round trip differs; `audit_verify_chain()` fails. Before reporting, walk this checklist and report each as PASS/FAIL/SKIP with evidence: login hardening, authorisation matrix, four-eyes, least privilege role, flag history append-only, human override survives re-QC, audit atomic and verified, approval snapshot, Zarr safe write, Zarr round trip, rejected data never in standardised, XSS, injection, limits documented, lint, Phase 1-2 regression.

**12. Docs.** Update README (Phase 3 section: how to log in, the dev-only seed users and why they are unsafe, how to review, how to publish), docs/architecture.md (replace the "open for Phase 3" note), component READMEs 04 and 05, .env.example. Add the Phase 3 limits to the docs (no SSO, no confidentiality classes, shared dev passwords, last-write-wins, no per-user locking, Zarr not tested on SeaweedFS, UI not browser-tested unless you did).

**13. Browser check (if a browser tool is available).** Open the UI against the running review-api (stand-ins) and walk login -> flag change -> approve once; report what you saw. If no browser is usable, say "UI not exercised in a browser" under Not verified. Do not claim UI behaviour from reading code.

## OUTPUT FORMAT (final report)

1. Files first: changed and added.
2. Short text with tables. State the assumptions you took and the choices you made (publish on approval vs explicit publish; auth library; Zarr version; chunking).
3. Folder tree of what changed.
4. "Phase 3 verification" table: one row per item in the task 11 checklist, PASS/FAIL/SKIP with evidence.
5. Numbers: tests passing before/after, number of endpoints and the role matrix size, mutation checks (each feature disabled -> which test failed), Zarr round-trip results per data type.
6. A separate "Not verified" list: Docker build/run, real PostGIS, SeaweedFS, the UI in a real browser (unless done), real users/SSO, load or many concurrent users, real data, anything else not run.
7. Do not push. Stop after the local commit. Do not start Phase 4.
