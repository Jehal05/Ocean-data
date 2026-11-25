# Phase 4 design: derived products and the data access API

Status: written before the code, then kept true (section 10 "as built" is checked against the code at the end).
Scope: components 06 (products pipeline) and 07 (data access API). Not in scope: dashboard (Phase 5), SSO/LDAP, propagation modelling, sediment DB, Kerchunk/VirtualiZarr, QC thresholds, the Phase 3 review rules.

## 1. Choices

| Topic | Choice | Why |
|---|---|---|
| Input to products | Only datasets with `status = published` and a `dataset_store` row, read with `scats_store.read_store` (every object checked against its manifest). Flags are the effective flags stored in the Zarr (they are the flags at the approved `review_version`). Staging and raw are never read | One source of truth; approved numbers only |
| "Good" data | Flag 1 or 2 (`good_flags` in the products config). Flags 3, 4, 9 are excluded from every product | Matches data_model section 6 |
| Interpolation | None. No smoothing, no extrapolation, no regridding | A product value is always a mean/min/max of real good values, or missing |
| Missing | NaN in floats, 0 in count arrays. A bin with fewer good values than its minimum count is missing, and the count is still reported | Users can see why it is missing |
| Directions | Variables whose units are `degree` get a circular (vector) mean and a count only; no min/max | An arithmetic mean of 350 and 10 degrees is 180, which is wrong |
| Product zone layout | `derived/<kind>/<dataset_key>/v<N>.zarr/` (Zarr format 2, Blosc/zstd, `scats_manifest.json` last) | N counts up; an old version stays in the store and the catalogue |
| Write | Same safe write as Phase 3 (temp prefix, verify, commit, manifest last), generalised in `scats_store.writer.write_zarr_safe` | One tested path |
| Determinism | No timestamps or host names inside a product. Same inputs + same code + same config give the same `content_sha256` and `manifest_sha256` | Rebuildable, checkable |
| Lineage | Catalogue row `derived_product` plus `lineage` JSON and the same facts in the Zarr attributes (see section 5) | Cite exactly what was used |
| Stale | A product is stale when its source dataset is no longer `published`, or its `dataset_store` row (store, manifest, review version) is no longer the one recorded. Marked by database trigger (stored) and by the view (computed). The API reports `stale` and refuses data from a stale product unless `allow_stale=true` | Old numbers are never silently current |
| Rebuild | Builds a new version; the old row gets `superseded_at`; nothing is deleted | History |
| Roles in the database | `scats_products` (pipeline) and `scats_access` (API), both without access to users, sessions, flags writes or reviews | Least privilege |
| API object model | Two kinds of collection: **datasets** (published standardised Zarr) and **products** (derived Zarr). Same routes, same limits | One code path |
| Lazy reads | A read-only Zarr store over S3 that fetches only the objects Zarr asks for and checks each against the manifest | A small window reads a few objects |
| Spatial search without PostGIS | Plain numeric bounding boxes (lat_min, lat_max, lon_min, lon_max) from the dataset attributes; longitudes are normalised to [-180, 180) and a box with west > east crosses the antimeridian. With PostGIS the same filter would use `ST_Intersects` on `dataset.bbox` and a GIST index; results should be the same for boxes, and the stand-in does not test PostGIS | The stand-in PostgreSQL has no PostGIS |
| Rate limit | Per user, counted in `query_log` under a per-user advisory lock | Needs no extra service |
| Query log | `query_log` (append-only, own table). Admin changes of limits and product builds also go to `audit_log` (hash chain). Downloads do not go to `audit_log` because every insert there takes one global lock | Auditable without a bottleneck |

## 2. Derived products

All definitions live in `components/06_products_pipeline/scats_products/products_config.yaml` (its sha256 is recorded in the lineage). `good` = flag 1 or 2 and a finite value.

| Kind | Input | Definition | Units | Partial bin / fill rule |
|---|---|---|---|---|
| `binned_profile` | profile datasets (Argo, CTD) | For each profile and each depth bin `[edge_i, edge_i+1)` (edges in config, metres), the mean of the good values of `temperature`, `salinity`, `sound_velocity` (those present) whose `depth` is inside the bin; `n_obs` per variable and bin. A profile whose position or time flag is not good is kept, but all its bins are missing | as the variable | Fewer than `min_obs` (config, default 1) good values: NaN, count kept |
| `series_hourly` | timeseries datasets | Per platform and UTC hour: mean, min, max, count of each good value; direction variables: circular mean and count only. The time axis is complete from the first to the last hour; hours without data have count 0 and NaN | as the variable | Fewer than `min_obs_hourly` (default 1): NaN |
| `series_daily` | timeseries datasets | Same per UTC day | as the variable | Fewer than `min_obs_daily` (default 18, which is 75 percent of 24 hourly values) good values: NaN |
| `sst_monthly_mean` | grid datasets with `sea_surface_temperature` | Per calendar month (UTC) and grid cell: mean of the good values on the source grid; `n_obs` per cell | degC | Fewer than `min_obs_monthly` (default 15) good values: NaN |
| bathymetry | - | **No product.** The standardised Zarr is one tile of 161 x 151 cells; the API subsets it directly (lazy), so a second copy adds nothing | - | - |

Every product has attributes: `product_kind`, `description`, `units` per variable, `flags_used` (`[1, 2]`), `min_obs_*` rule, `bin_definition`, `interpolation: none`, and the lineage (section 5).

## 3. Database (migration 007, additive)

* `derived_product`: one row per build (`product_id`, `kind`, `product_key`, `version`, `dataset_id`, source store facts, `uri`, `content_sha256`, `manifest_sha256`, `code_sha256`, `config_sha256`, `lineage`, extent columns, `built_at`, `built_by`, `superseded_at`, `stale_at`, `stale_reason`). Unique (`kind`, `dataset_id`, `version`); at most one row per (`kind`, `dataset_id`) with `superseded_at IS NULL`.
* `catalogue_dataset` and `catalogue_product` views: what the API may read (published only; computed `stale`; extent as plain numbers). Views are owned by the superuser, so `scats_access` needs no right on the base tables.
* `api_limit` (role, name, value) with a hard `CHECK` range; `query_log` (append-only; UPDATE/DELETE/TRUNCATE blocked by trigger).
* Trigger: when `dataset.status` leaves `published` or a `dataset_store` row changes, products of that dataset get `stale_at`.
* `scats_products`: SELECT on `dataset`, `dataset_store`, `storage_zone`; INSERT on `derived_product` and `audit_log`; UPDATE only (`superseded_at`) on `derived_product`.
* `scats_access`: SELECT on the two views and `api_limit`; INSERT on `query_log` and `audit_log`; UPDATE (`value`, `updated_by`, `updated_at`) on `api_limit`; EXECUTE on `auth_login`, `auth_validate_token`, `auth_logout`. No access to `app_user`, `auth_session`, `login_attempt`, `observation`, `qc_flag`, `review`, `dataset` or `derived_product` base tables.

## 4. API routes and roles (one table, `access_api/policy.py`; unlisted route = 403)

| Method, path | Role | Notes |
|---|---|---|
| GET `/health/live` | public | process only |
| GET `/health` | public | Phase 1 contract: booleans only (`database.ok`, `storage.ok`), no names or error text |
| GET `/health/audit` | admin | full audit chain |
| POST `/auth/login`, POST `/auth/logout`, GET `/auth/me` | public / viewer / viewer | same accounts and tokens as the review API |
| GET `/catalogue` | viewer | Phase 1 route, now published datasets only |
| GET `/datasets`, `/products` | viewer | search: `q`, `platform_type`, `dataset_type`, `variable`, `time_from`, `time_to`, `bbox`, `include_stale`, page |
| GET `/datasets/{key}`, `/products/{key}` | viewer | metadata, lineage, flags used, versions |
| GET `/datasets/{key}/zarr`, `/products/{key}/zarr` | viewer | object listing and manifest checksum (no data) |
| GET `/datasets/{key}/subset`, `/products/{key}/subset` | viewer | JSON page of rows |
| GET `/datasets/{key}/download`, `/products/{key}/download` | viewer | `format=netcdf` or `csv` |
| GET `/limits` | viewer | the limits that apply to the caller |
| PUT `/admin/limits/{role}/{name}` | admin | change a limit inside its hard range; audited |

Reviewer and viewer see the same data (no confidentiality classes yet; open point 2).

## 5. Lineage record

```
{ "kind", "product_key", "version", "code_version", "code_sha256", "config_sha256",
  "source": {"dataset_key", "dataset_id", "store_uri", "store_manifest_sha256", "store_content_sha256",
             "review_id", "review_version", "qc_config_version", "published_at"},
  "flags_used": [1, 2], "parameters": {...the config used...} }
```
`code_sha256` is the sha256 over the source files of `scats_products`. Rebuild = run the pipeline again for the same dataset: same lineage gives the same `content_sha256`.

## 6. Limits (defaults per role; admin may change inside the hard range)

| Name | Default | Hard max | Meaning |
|---|---|---|---|
| `page_size_max` | 200 | 1000 | rows per JSON page |
| `max_rows_subset` | 5000 | 20000 | rows one subset call may select (refused above, never cut) |
| `max_rows_download` | 500000 | 5000000 | rows one download may select |
| `max_bytes_download` | 104857600 | 1073741824 | size of the produced file |
| `max_time_span_days` | 366 | 3660 | time range of one request (a request without time filter on a dataset longer than this is refused) |
| `max_bbox_area_deg2` | 10000 | 64800 | area of the box |
| `request_timeout_s` | 30 | 300 | checked between steps; a running read is not cancelled |
| `rate_per_minute` | 60 | 6000 | requests per user per minute (HTTP 429) |

Over a limit the answer is 413 (size, rows, span, area), 422 (bad value) or 429, with the limit and the value in the message. A page that is not the last says `truncated: true` and `next_page`. The rate limit answers 429 with `Retry-After`.

## 7. Failure behaviour

* Product build: same stages as Phase 3 (`PublishError(stage, detail, state)`); the catalogue row is inserted only after the objects are verified in the final prefix. A failure before that leaves the old version current. A failure after the objects and before the row leaves unreferenced objects at `v<N>`; the next build reuses N and clears them first.
* Query log: the log row is written in the transaction that re-reads the catalogue row (`FOR SHARE` on the view's table is not possible, so the row is re-selected under a per-user advisory lock). If the insert fails the call returns 503 and no data.
* Corrupt or changed object in the store: 502 with the object name; nothing is returned.

## 8. Security

Authentication reuses `scats_auth` (Bearer or the HttpOnly SameSite=Strict cookie; cookie + unsafe method needs `X-Requested-With: scats-ui`). Filters are checked against fixed vocabularies and numeric ranges and applied in numpy or as SQL parameters; filter text never reaches a path, an S3 key (keys come from the catalogue and the manifest) or a shell. Downloads get server-made file names, `nosniff`, `Content-Disposition: attachment` and `no-store`. CSV string cells that start with `=`, `+`, `-`, `@`, tab or CR get a leading apostrophe (numbers are written as numbers). JSON has no NaN (null).

## 9. Open points for NPOL

1. Which derived products they need first (T-S diagrams, MLD, heat content and sound speed are not built). 2. Confidentiality classes. 3. Depth bin edges and minimum counts. 4. Whether a stale product should be rebuilt automatically. 5. Download size and rate limits that suit the network. 6. Their own login system.

## 10. As built

Where the code differs from sections 1 to 9 (the code is right, the sections above are the plan):

* `/health` is public and returns booleans only (the Phase 1 acceptance script needs it); `/health/audit` is admin.
* Series products exclude records whose position or time flag is bad (not only the value's own flag). Profiles with a bad position flag give no values.
* `qc_config_sha256` in the lineage and in API answers is filled only if the shipped QC config has the same version as the dataset's review; otherwise it is null (never a guess).
* `scats_access` also has SELECT on `audit_log` (the insert trigger reads the last chain row) and EXECUTE on `access_set_limit`, `access_recent_count` (SECURITY DEFINER) and the auth and audit-verify functions. Limits are changed through `access_set_limit`, which checks an admin token and writes an audit row `limit.change`.
* Refused requests (bad filter, over a limit, 429) are not written to `query_log`; answered ones are. `query_log` is written by `_log_checked`, which re-reads the catalogue row in the same transaction; any failure gives 503 and no data. A manifest or content change between the first read and the log gives 409.
* Rate limit is 429 with `Retry-After: 60`; time budget overrun is 504.
* Downloads (CSV and NetCDF) are flattened to one dimension `row` and built in memory (bounded by `max_bytes_download`), not streamed.
* Lazy reads: `scats_store.lazy.S3ReadStore` serves only manifest keys, checks size and sha256 of each object, counts what it fetched, and stops at the time budget between reads. A mismatch is 502 and nothing is returned.
* Stale: stored mark by trigger, plus a computed mark in the view. Reverting a source does not clear a stored mark; only a rebuild does.
* `build_all` catches an error per build, so one failing dataset does not stop the others.
* Existing code changed: `scats_store.writer` (`write_zarr_safe` extracted from `publish_zarr`, which now calls it; behaviour and Phase 3 tests unchanged), `tests/db_stub.py` (reset also covers the new tables), `pytest.ini` (paths), `docker-compose.yml`, `.env.example`, `components/07_data_access_api/app/main.py` (now a re-export). Migrations 001 to 006 are untouched.
* Not verified: Docker images and the stack, SeaweedFS, PostGIS SQL, load and concurrency at scale, real (non-synthetic) data.
