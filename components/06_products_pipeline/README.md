# 06 Derived Products Pipeline

**Phase 4, built.** Design: `docs/phase4_design.md`. Definitions: `scats_products/products_config.yaml` (its sha256 goes into every lineage record).

Input: **only datasets with status `published`** and a `dataset_store` row, read with `scats_store.read_store` (every object checked against the manifest). Staging and raw are never read. Output: the derived zone, `derived/<kind>/<dataset_key>/v<N>.zarr/`, written with the same safe write as Phase 3 (temp prefix, verify, copy to the final prefix, manifest last).

| Kind | For | What it is |
|---|---|---|
| `binned_profile` | Argo, CTD | mean per fixed depth bin of temperature, salinity, sound velocity (good flags 1, 2 only) with `_n_obs` |
| `series_hourly`, `series_daily` | buoys | mean, min, max, count per UTC hour or day; direction variables get a circular mean and a count |
| `sst_monthly_mean` | SST grids | monthly mean per cell on the source grid, with `_n_obs` |

No interpolation anywhere: a bin or cell with fewer good values than its minimum is missing, and its count is still stored. No product for bathymetry.

```
python -m scats_products build [--dataset KEY] [--force]
```
Environment: `PRODUCTS_DATABASE_URL` or `DATABASE_URL` (role `scats_products`), `S3_*`. Without `--force` a build whose source, code and config are unchanged does nothing ("unchanged"). A rebuild adds version N+1 and sets `superseded_at` on the old row; nothing is deleted. A product is **stale** when its source dataset leaves `published` or its store row (manifest, review version) changes; a database trigger stores the mark and the catalogue view also computes it. Same inputs, code and config give the same `content_sha256` (no timestamps inside products).

Not built: T-S diagrams, vertical sections, mixed-layer depth, heat content, sound-speed products, propagation parameters, climatology, trends (open points for NPOL). Docker image not built or run.
