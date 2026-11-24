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

## Start the stack

Needs Docker with Compose v2. From this folder:

```bash
docker compose up --build -d      # starts PostGIS, MinIO, creates 3 buckets, starts the API
docker compose ps                 # db, minio, api should be running; minio-init exits 0
curl http://localhost:8000/health # expect "status": "ok"
```

| Service | URL | Default login |
|---|---|---|
| API docs | http://localhost:8000/docs | none |
| MinIO console | http://localhost:9001 | `scatsminio` / `scatsminio_dev_pw` |
| PostgreSQL | `localhost:5432`, db `scats` | `scats` / `scats_dev_pw` |

Seed app users (POC only, change before real use): `admin/admin123`, `reviewer/reviewer123`, `viewer/viewer123`.

Copy `.env.example` to `.env` to change ports or passwords.
The database schema in `db/init/` runs only the first time the database volume is created. To rebuild it from scratch: `docker compose down -v` then `docker compose up --build -d`.

Stop: `docker compose down` (add `-v` to delete stored data).

## Load the sample data

```bash
pip install -r requirements-data.txt

# 1. Get data (pick one)
python components/01_data_sources/make_synthetic_data.py      # offline, SYNTHETIC, ready now
python components/01_data_sources/fetch_public_data.py        # real public data, needs internet

# 2. Build the error-injected test set + answer key
python components/01_data_sources/inject_errors.py

# 3. Put files in the raw zone, byte-for-byte, with checksums
python scripts/upload_raw.py
python scripts/upload_raw.py --src data/test_errors --prefix test_errors/
```

The repo already contains the generated synthetic files in `data/raw` and `data/test_errors`, so step 1 and 2 can be skipped; step 3 is needed once the stack is up.

**The bundled data is synthetic.** It looks like real products (units, fill values, packing) but is generated. Every file says so. Real data needs the internet; see `components/01_data_sources/README.md`.

## Check Phase 1

```bash
python tests/check_phase1.py
```

Prints PASS/FAIL for each acceptance criterion against the running stack.

## Folder map

```
docker-compose.yml          the stack
db/init/001_schema.sql      catalogue, QC flags, users, audit log
docs/architecture.md        component diagram, tech choices, scope
docs/data_model.md          common data model (draft) and open questions for NPOL
components/                 one folder per component (01 to 08)
  01_data_sources/            sample data scripts
  07_data_access_api/         FastAPI skeleton (health + catalogue)
  others                      placeholders with a README for their phase
scripts/                    init_buckets.sh, upload_raw.py
data/raw/                   sample data, five files, four dataset types
data/test_errors/           corrupted copies + answer_key.csv (24 errors)
tests/check_phase1.py       acceptance check
```
