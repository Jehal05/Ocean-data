# Demo script (about 10 minutes)

All data is synthetic (see `docs/data_sources.md`). The stand-ins are PostgreSQL 16 without PostGIS and moto instead of SeaweedFS. No Docker is needed.

## Start (one command)

```bash
python3 tests/dash_stack.py --port 18080
```

It starts the database stand-in and an S3 stand-in, publishes the 16 files of the realistic synthetic set (`data/realistic`, 2024–2025; `--data small` loads the five small files of `data/raw`) through the real review flow, about 95 s, builds the products, serves the API, and prints `READY http://127.0.0.1:18080`. Open `http://127.0.0.1:18080/app`. Stop with Ctrl-C. Needs the Python packages of components 02-07 and local PostgreSQL 16 binaries (the same ones the tests use).

## Steps

1. **Login.** The page warns that development passwords are in use. Show the theme button (system, light, dark). Sign in as `viewer` (a development account; its published password is in `db/init/003_*`). Point out: a wrong password gives a plain message; five wrong ones lock the account for 15 minutes.
2. **Map.** Sixteen footprints on a graticule (the Argo footprint is stretched by two deliberately bad positions: a good moment to explain QC) (no coastline: none is licensed in the repo). Filter by platform type `argo`; type a bad box such as `10,20` to show the plain-words refusal. Point at a footprint (card), Tab to one, Enter, Escape. Footer: "synthetic".
3. **Profile.** Dataset `argo_synthetic_arabian_sea_2024_2025`. Give a range of at most a year (for example 2024-06-01 to 2024-08-31; the API refuses longer ranges and the page says so). The picker lists the profiles of that range; pick one. Temperature and salinity against depth. Switch "Quality" to "all": flagged points appear with a different shape and a label. Move the pointer over a chart: crosshair, readout and table row follow; the strip under it has Copy and "Copy figure caption". Show the table alternative.
4. **Series.** `series_daily` of a buoy (`AS02`), 2024-04-01 to 2024-09-30: the June cyclone shows as a pressure dip, outages as hatched gaps. `series_hourly` works for a month or two: line, min-max band, count bars; a hatched strip where no good value exists. Drag across the chart to zoom. Direction variables show a circular mean and no band.
5. **SST.** Box `50,5,54,8` (Somali upwelling), then the month slider over 24 monthly means (a bigger box stops at the 5000-row limit and the page says so); press Play and watch it stop at the last month; hover a cell for its readout. Try a very large box: the API's own refusal is shown.
6. **Explore.** Pick a dataset, apply a filter (a platform such as `SYN-A001` and a date range keep it under 5000 rows), preview the first page ("page 1 of N" notice), download CSV and NetCDF. The file name is chosen by the server.
7. **Provenance strip** under any chart: data_version, product_version, qc_config_sha256, flags used. This is what an analyst quotes; "Copy figure caption" puts it on one line. Finish with the narrow window (360 px: bottom bar) and dark theme.
8. **Roles.** As viewer, an admin-only API route such as `/health/audit` is refused with 403 and the reason "the admin role is required".

## What to say about limits

PostgreSQL without PostGIS, moto instead of SeaweedFS, no Docker run, synthetic data only, tested in headless Chrome 153 only.
