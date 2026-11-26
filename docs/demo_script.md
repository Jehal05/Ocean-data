# Demo script (about 10 minutes)

All data is synthetic. The stand-ins are PostgreSQL 16 without PostGIS and moto instead of SeaweedFS. No Docker is needed.

## Start (one command)

```bash
python3 tests/dash_stack.py --port 18080
```

It starts the database stand-in and an S3 stand-in, publishes the five synthetic files through the real review flow, builds the products, serves the API, and prints `READY http://127.0.0.1:18080`. Open `http://127.0.0.1:18080/app`. Stop with Ctrl-C. Needs the Python packages of components 02-07 and local PostgreSQL 16 binaries (the same ones the tests use).

## Steps

1. **Login.** The page warns that development passwords are in use. Sign in as `viewer` (a development account; its published password is in `db/init/003_*`). Point out: a wrong password gives a plain message; five wrong ones lock the account for 15 minutes.
2. **Map.** Five footprints on a graticule (no coastline: none is licensed in the repo). Filter by platform type `argo`; type a bad box such as `10,20` to show the plain-words refusal. Footer: "synthetic".
3. **Profile.** Dataset `argo...`; pick a profile. Temperature and salinity against depth. Switch "Quality" to "all": flagged points appear with a different shape and a label. Show the table alternative.
4. **Series.** `series_hourly` of the buoy: line, min-max band, count bars; a gap where no good value exists. Direction variables show a circular mean and no band.
5. **SST.** Month selector over the monthly mean grid. Try a very large box: the API's own refusal is shown.
6. **Explore.** Pick a dataset, apply a filter, preview the first page ("page 1 of N" notice), download CSV and NetCDF. The file name is chosen by the server.
7. **Citation block** on any view: data_version, product_version, qc_config_sha256, flags used. This is what an analyst quotes.
8. **Roles.** As viewer, an admin-only API route such as `/health/audit` is refused with 403 and the reason "the admin role is required".

## What to say about limits

PostgreSQL without PostGIS, moto instead of SeaweedFS, no Docker run, synthetic data only, tested in headless Chrome 153 only.
