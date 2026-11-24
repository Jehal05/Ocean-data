# SCATS Ocean Data Platform: POC

Proof of concept for the NPOL / DRDO tender "Ocean Environment and Acoustic
Intelligence System for Geo-spatial Analysis and Target-Based Modelling"
(GeM bid GEM/2026/B/8266583).

**Current status: Phase 1 of 5, foundation and data model.**

| Phase | Content | Status |
|---|---|---|
| 1 | Foundation: architecture, data model, stack, schema, sample data, error test set | **this delivery** |
| 2 | Parser API and QC framework | not started |
| 3 | Scientific data store and expert review interface | not started |
| 4 | Derived products pipeline and Data Access API | not started |
| 5 | Visualisation dashboard, hardening, demo | not started |

## What has and has not been verified

The Docker stack has **not yet been started end to end** by the author (no Docker daemon was available while building). Tested separately: the database schema and roles (on PostgreSQL 16 with PostGIS replaced by plain types), the API and upload script (against an S3 emulator), the data scripts and the answer key. Untested: the PostGIS-specific SQL, the SeaweedFS service settings, `fetch_public_data.py`. Run `docker compose up --build -d` and then `python tests/check_phase1.py`; fix anything it reports.

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

## Check Phase 1

```bash
python tests/check_phase1.py
```

Prints PASS / FAIL / SKIP per acceptance criterion. SKIP means "could not be verified here", never "passed".

## Folder map

```
docker-compose.yml          the stack
config/seaweedfs-s3.json    S3 credentials for the dev object store
db/init/                    001 schema, 002 limited API role, 003 dev seed users
docs/architecture.md        component diagram, tech choices, scope, open design decision
docs/data_model.md          common data model (draft) and open questions for NPOL
components/                 one folder per component (01 to 08)
  01_data_sources/            sample data scripts
  07_data_access_api/         FastAPI skeleton (health + catalogue) and storage init
  others                      placeholders with a README for their phase
scripts/                    upload_raw.py
data/raw/                   sample data, five files, four dataset types
data/test_errors/           corrupted copies + answer_key.csv (24 errors, 0-based indices)
tests/check_phase1.py       acceptance check
```
