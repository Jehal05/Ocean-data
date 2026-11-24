# 07 Data Access API

**Phase 1: skeleton only.** FastAPI app in `app/main.py`.

| Endpoint | What it does now |
|---|---|
| `GET /health/live` | Process is up |
| `GET /health` | Checks database (core tables present, audit chain intact) and the three storage buckets. Returns `ok` or `degraded` |
| `GET /catalogue` | Lists datasets from the catalogue (empty until Phase 2) |
| `GET /docs` | OpenAPI documentation |

Phase 4 adds discovery, metadata search, spatial and temporal subsetting, profile extraction, time series and product download.

Run on its own: `pip install -r requirements.txt && uvicorn app.main:app --port 8000` from this folder, with `DATABASE_URL` and the `S3_*` variables set (see `docker-compose.yml`).
