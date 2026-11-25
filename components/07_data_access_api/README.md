# 07 Data Access API

**Phase 4, built.** FastAPI package `access_api` (`app/main.py` re-exports it for the Phase 1 acceptance script). Design: `docs/phase4_design.md`.

Connects as the limited database role `scats_access`: it reads only the views `catalogue_dataset` (published datasets) and `catalogue_product`, plus `api_limit`; it can insert into `query_log`. Accounts and tokens are the review API's (`libs/scats_auth`; Bearer token or HttpOnly cookie).

**Routes** (one table in `access_api/policy.py`; a route not in it returns 403; `/docs` and `/openapi.json` are off)

| Method, path | Minimum role |
|---|---|
| GET `/health/live` | public |
| GET `/health` | public |
| GET `/health/audit` | admin |
| POST `/auth/login` | public |
| POST `/auth/logout` | viewer |
| GET `/auth/me` | viewer |
| GET `/catalogue` | viewer |
| GET `/datasets` | viewer |
| GET `/datasets/{key}` | viewer |
| GET `/datasets/{key}/zarr` | viewer |
| GET `/datasets/{key}/subset` | viewer |
| GET `/datasets/{key}/download` | viewer |
| GET `/products` | viewer |
| GET `/products/{key}` | viewer |
| GET `/products/{key}/zarr` | viewer |
| GET `/products/{key}/subset` | viewer |
| GET `/products/{key}/download` | viewer |
| GET `/limits` | viewer |
| PUT `/admin/limits/{role}/{name}` | admin |

Data routes take `bbox=west,south,east,north`, `time_from`, `time_to`, `depth_min`, `depth_max`, `platform`, `variable`, `quality=good|all`, `page`, `page_size`; downloads take `format=netcdf|csv`. Values are checked against strict patterns and ranges; anything else is 422.

**Limits** (per role in `api_limit`; an admin may change them inside the hard maximum)

| Limit | Default | Hard maximum | Meaning |
|---|---|---|---|
| `page_size_max` | 200 | 1000 | rows per JSON page |
| `max_rows_subset` | 5000 | 20000 | rows one subset call may select (refused above, never cut) |
| `max_rows_download` | 500000 | 5000000 | rows one download may select |
| `max_bytes_download` | 104857600 | 1073741824 | size of the produced file |
| `max_time_span_days` | 366 | 3660 | time range of one request (a request without a time filter on a longer collection is refused) |
| `max_bbox_area_deg2` | 10000 | 64800 | area of the bounding box in square degrees |
| `request_timeout_s` | 30 | 300 | time budget; checked between reads, a running read is not cancelled |
| `rate_per_minute` | 60 | 6000 | requests per user per minute (HTTP 429) |

* Over a limit the answer is 413, 422 or 429 with the limit and the value; a result is never cut silently.
* A page that is not the last says truncated=true and next_page.
* Time budget (request_timeout_s) is checked between reads: a read that is already running is not cancelled.
* Refused requests (bad filter, over a limit) are not written to the query log; only answered requests are.
* Dataset extents come from the dataset attributes as numbers; a dataset that crosses 180 degrees longitude is listed by its min/max and may match more boxes than it should.
* NetCDF and CSV downloads hold the same rows (one dimension 'row'); gridded data is not rebuilt into a grid.

Every answered request writes one `query_log` row in the same transaction that re-reads the catalogue row; if that fails the answer is 503 and no data is returned. Data is read lazily from the Zarr store: only the objects a request needs are fetched and each is checked against the manifest. CSV text cells that start with `=`, `+`, `-`, `@`, tab or CR get a leading apostrophe; JSON has no NaN; file names are made by the server; answers carry `nosniff` and `no-store`.

Bbox search uses plain latitude/longitude numbers (no PostGIS in the stand-in database); with PostGIS it would use `ST_Intersects` and a spatial index. Docker image not built or run.

Run on its own: `pip install -r requirements.txt && uvicorn access_api.main:app --port 8000` with `DATABASE_URL` (role `scats_access`) and the `S3_*` variables set. `python -m app.init_storage` creates the zone buckets (the `storage-init` service).
