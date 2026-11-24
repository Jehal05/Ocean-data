# 07 Data Access API

**Phase 1: skeleton only.** FastAPI app in `app/main.py`.

| Endpoint | What it does now |
|---|---|
| `GET /health/live` | Process is up |
| `GET /health` | Checks the database (core tables present, newest 1000 audit rows intact) and the three storage buckets. Returns `ok` or `degraded` |
| `GET /health/audit` | Verifies the whole audit-log hash chain (slow on a large log) |
| `GET /catalogue` | Lists datasets from the catalogue (empty until Phase 2) |
| `GET /docs` | OpenAPI documentation |

Phase 4 adds discovery, metadata search, spatial and temporal subsetting, profile extraction, time series and product download.

The API connects as the limited database role `scats_app` (read the catalogue, append to the audit log), never as the superuser.

`python -m app.init_storage` creates the zone buckets (run by the `storage-init` service).

Run on its own: `pip install -r requirements.txt && uvicorn app.main:app --port 8000` from this folder, with `DATABASE_URL` and the `S3_*` variables set (see `docker-compose.yml`).
