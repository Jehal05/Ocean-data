# SCATS Ocean Data Platform: POC

Proof of concept for the NPOL / DRDO tender "Ocean Environment and Acoustic
Intelligence System for Geo-spatial Analysis and Target-Based Modelling"
(GeM bid GEM/2026/B/8266583).

**Current status: Phase 2 of 5, parser API and QC framework (Phase 1 foundation underneath).**

| Phase | Content | Status |
|---|---|---|
| 1 | Foundation: architecture, data model, stack, schema, sample data, error test set | done |
| 2 | Parser API and QC framework | **this delivery** |
| 3 | Scientific data store and expert review interface | not started |
| 4 | Derived products pipeline and Data Access API | not started |
| 5 | Visualisation dashboard, hardening, demo | not started |

## Phase 2: parsers, QC and ingest

Review round (config 0.2.0): empty `INGEST_API_KEY` disables `/ingest` (503); the ingest role can update only status columns (migration 005); synthetic status is declared in the file (`# synthetic: true` / attribute `is_synthetic`), and `unknown` is valid; CSV unit conversions use a `# units: col=unit, ...` header, otherwise `units_assumed_from_mapping` is set; QC does not read the clock (reference time is recorded); time jumps now flag the wrong record. A blind error set (`data/blind_errors`) was scored once: `reports/phase2_blind_score.md`. Other reports: `phase2_qc_score.md`, `phase2_mutation_check.md`. Details and limits: `docs/phase2_design.md` sections 7 and 8.

```bash
docker compose down -v          # ONLY if you already started Phase 1: db/init runs on a fresh volume only (deletes data)
docker compose up --build -d    # now also starts parser-api on http://localhost:8001
curl localhost:8001/parsers
# POST /ingest needs a key: set INGEST_API_KEY in .env (see .env.example), or INGEST_ALLOW_ANONYMOUS=true for local development only
curl -X POST localhost:8001/ingest -H "X-API-Key: $INGEST_API_KEY" -H 'content-type: application/json' -d '{"path": "/srv/data/test_errors/buoy_met_wave_errors.csv"}'
curl -H "X-API-Key: $INGEST_API_KEY" localhost:8001/datasets/buoy_met_wave_errors/qc-summary
python tests/check_phase2.py    # acceptance check, PASS / FAIL / SKIP with evidence
python tests/score_qc.py        # recall against the answer key, writes reports/phase2_qc_score.md
```

* Five parsers (Argo NetCDF, ship CTD CSV, buoy CSV, SST grid, bathymetry grid) turn files into the common data model. Names, units and fill values live in `components/02_parser_api/scats_parsers/mappings/*.yaml`.
* Ten automated QC tests with a versioned config, `components/03_qc_framework/scats_qc/qc_config.yaml`. **Every threshold has a rationale and a source there; several sources are marked "recalled, not re-fetched".**
* Ingest writes a staging NetCDF (full flags), PostgreSQL rows (datasets, observations, non-good flags) and audit entries, and is safe to repeat.
* The service connects as `scats_ingest` (migration `db/init/004_ingest_role.sql`): not a superuser, can add flags but never change or delete them. `db/init` only runs on a **fresh** volume, hence `docker compose down -v` above.
* Design and every later change: `docs/phase2_design.md`.
* **Local, pre-push commit only.** Nothing has been pushed.

## What has and has not been verified

The Docker stack has **not yet been started end to end** by the author (no Docker daemon was available while building). Tested separately: the database schema and roles (on PostgreSQL 16 with PostGIS replaced by plain types), the API and upload script (against an S3 emulator), the data scripts and the answer key. Untested: the PostGIS-specific SQL, the SeaweedFS service settings, `fetch_public_data.py`. Run `docker compose up --build -d` and then `python tests/check_phase1.py`; fix anything it reports.

**Phase 2 additionally:** the `parser-api` image and its compose service were validated with `docker compose config` but **never built or started**. Database behaviour was tested on a stand-in PostgreSQL 16 *without PostGIS* (geography columns replaced by text, a shim for `ST_GeogFromText`), so the point and polygon columns and spatial indexes are untested. All data is synthetic, so the 24/24 recall shows the tests find the planted error kinds, not that they work on real observations. The blind error set was written by the same author on synthetic data. Thresholds for the Argo-derived tests were recalled from memory; verify them against the current Argo Quality Control Manual before relying on them.

## Start the stack

Needs Docker with Compose v2. From this folder:

```bash
docker compose up --build -d      # PostGIS, SeaweedFS (S3), bucket creation, API
docker compose ps                 # db, storage, api running; storage-init exited (0)
curl http://localhost:8000/health # expect "status": "ok"
```

| Service | URL (all bound to localhost only) | Notes |
|---|---|---|
| API docs | http://localhost:8000/docs | |
| S3 API | http://localhost:8333 | keys `scatsdev` / `scatsdev_secret_pw` (dev only; see `config/seaweedfs-s3.json`) |
| SeaweedFS status | http://localhost:9333 | |
| PostgreSQL | `localhost:5432`, db `scats` | superuser `scats` / `scats_dev_pw`; API role `scats_app` |

Dev seed users (`admin/admin123`, `reviewer/reviewer123`, `viewer/viewer123`) come from `db/init/003_dev_seed_users.sql`. **Delete that file before any real deployment.** Change every default password before exposing anything beyond your own machine.

Copy `.env.example` to `.env` to change ports or passwords. The images are unpinned on purpose until pulled once: then run `docker compose images` and pin the tags in `.env` (`S3_IMAGE=...`).

The scripts in `db/init/` run only when the database volume is first created. To rebuild from scratch: `docker compose down -v` then `docker compose up --build -d`.

Stop: `docker compose down` (add `-v` to delete stored data).

## Load the sample data

```bash
pip install -r requirements-data.txt

# 1. Get data (pick one)
python components/01_data_sources/make_synthetic_data.py      # offline, SYNTHETIC, ready now
python components/01_data_sources/fetch_public_data.py        # real public data, needs internet (untested)

# 2. Build the error-injected test set + answer key
python components/01_data_sources/inject_errors.py

# 3. Put files in the raw zone, byte-for-byte, with checksums
python scripts/upload_raw.py
python scripts/upload_raw.py --src data/test_errors --prefix test_errors/
```

The repo already contains the generated synthetic files in `data/raw` and `data/test_errors`, so steps 1 and 2 can be skipped; step 3 is needed once the stack is up.

**The bundled data is synthetic.** It looks like real products (units, fill values, packing) but is generated. Every file says so. See `components/01_data_sources/README.md`.

## Check Phase 1 (still valid)

```bash
python tests/check_phase1.py
```

Prints PASS / FAIL / SKIP per acceptance criterion. SKIP means "could not be verified here", never "passed".

## Folder map

```
docker-compose.yml          the stack
config/seaweedfs-s3.json    S3 credentials for the dev object store
db/init/                    001 schema, 002 limited API role, 003 dev seed users, 004 ingest role, 005 ingest rights narrowed (Phase 2)
docs/architecture.md        component diagram, tech choices, scope, flag-storage decision
docs/phase2_design.md       Phase 2 design note and the changes made after it
docs/data_model.md          common data model (draft) and open questions for NPOL
components/                 one folder per component (01 to 08)
  01_data_sources/            sample data scripts
  02_parser_api/              parsers, YAML mappings, ingest service, FastAPI app, Dockerfile
  03_qc_framework/            ten QC tests, engine, versioned config
  07_data_access_api/         FastAPI skeleton (health + catalogue) and storage init
  others                      placeholders with a README for their phase
libs/scats_common/          shared data model, units, flag scheme, validate_dataset()
scripts/                    upload_raw.py
reports/                    phase2_qc_score.json / .md (scoring output)
data/raw/                   sample data, five files, four dataset types
data/test_errors/           corrupted copies + answer_key.csv (24 errors, 0-based indices)
tests/check_phase1.py       Phase 1 acceptance check
tests/check_phase2.py       Phase 2 acceptance check
tests/score_qc.py           scoring harness (the only code that reads answer_key.csv)
tests/unit, tests/integration, tests/db_stub.py   pytest suites and the PostgreSQL stand-in
```
