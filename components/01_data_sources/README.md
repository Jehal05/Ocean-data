# 01 Data Sources

Scripts that produce the sample data. Region 8-24N, 60-75E (Arabian Sea / northern Indian Ocean), window 2025-01-01 to 2025-03-31.

## The four dataset types

| Type | Synthetic file (in `data/raw`) | Format | Real source (`fetch_public_data.py`) |
|---|---|---|---|
| Profiles (T, S) | `argo_profiles/argo_synthetic_arabian_sea_2025Q1.nc` | NetCDF, Argo-style, 60 profiles x 80 levels, fill value 99999 | Argo via IFREMER ERDDAP (CSV) |
| Profiles (ship CTD, T, S, sound speed) | `ship_ctd/ctd_casts_synthetic_arabian_sea_2025.csv` | CSV long form, `#` header lines, fill -999 | none (own cruise data) |
| Gridded SST | `sst_grid/sst_synthetic_0p25deg_2025Q1.nc` | NetCDF, kelvin, packed int16, land masked | NOAA OISST via CoastWatch ERDDAP |
| Wind / wave / current / met | `buoy_met_wave/buoy_met_wave_synthetic_2025Q1.csv` | CSV, hourly, mixed units | ERA5 via Copernicus CDS (needs free login) |
| Bathymetry | `bathymetry/bathymetry_synthetic_0p1deg.nc` | NetCDF, elevation (negative below sea level) | ETOPO via ERDDAP, or GEBCO (manual) |

Formats deliberately differ (names, units, fill values, packing, time encoding) so the Phase 2 parsers have real work to do.

## Scripts

| Script | Purpose | Tested here? |
|---|---|---|
| `make_synthetic_data.py` | Offline synthetic data, fixed random seed, labelled SYNTHETIC | Yes |
| `inject_errors.py` | Corrupted subsets plus `answer_key.csv`, with 25 self-checks | Yes |
| `fetch_public_data.py` | Real public data | **No.** Written without access to those servers. Dataset IDs may need adjusting. |

## The error-injected test set (`data/test_errors`)

24 deliberate errors across three files. `answer_key.csv` lists each one (file, record, variable, level, type, original and injected value, and the QC test expected to catch it).

| Error type | Count | Expected QC test |
|---|---|---|
| spike | 5 | `spike` |
| out_of_range | 5 | `global_range` |
| missing | 5 | `missing_value` |
| position_on_land | 2 | `position_on_land` |
| position_invalid | 1 | `position_range` |
| timestamp_duplicate | 2 | `timestamp_monotonic` |
| timestamp_non_monotonic | 2 | `timestamp_monotonic` |
| density_inversion | 2 | `density_inversion` |

Everything else in those files is untouched clean data. A good QC engine in Phase 2 should catch every row of the key and should not flag clean records (profile 0 and the first CTD cast are clean controls).

Some injections can also trip a second test (for example a large temperature jump that causes an inversion). The key records the primary expected test; Phase 2 scoring should accept any additional correct flag on the same record.

## Honesty note

Synthetic data is not physically validated. It is smooth, stably stratified and plausible in range, which is enough to test software but not to say anything about the ocean.
