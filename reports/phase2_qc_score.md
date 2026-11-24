# Phase 2 QC score (original error set)

Database: local stand-in PostgreSQL 16 WITHOUT PostGIS (tests/db_stub.py)
QC config version: 0.2.0

**Recall: 24/24**   |   false positives in error files: 18   |   collateral density flags: 21 around 11 culprit cells   |   flags 3/4 on the five clean raw files: 0

| Error type | Detected | Total |
|---|---|---|
| density_inversion | 2 | 2 |
| missing | 5 | 5 |
| out_of_range | 5 | 5 |
| position_invalid | 1 | 1 |
| position_on_land | 2 | 2 |
| spike | 5 | 5 |
| timestamp_duplicate | 2 | 2 |
| timestamp_non_monotonic | 2 | 2 |

| Magnitude band | Correct | Total |
|---|---|---|
| unbanded | 24 | 24 |

Expected primary test fired on the cell (any finding, not only the winner): 24/24

## Misses

none

## Negative controls flagged (should have been left alone)

none

## False positives (flag 3/4 on a cell the key does not explain)

* argo_profiles_errors.nc record 3 salinity level 39 flag 3 by density_inversion
* argo_profiles_errors.nc record 3 salinity level 40 flag 3 by density_inversion
* argo_profiles_errors.nc record 3 temperature level 39 flag 3 by density_inversion
* argo_profiles_errors.nc record 5 salinity level 10 flag 3 by density_inversion
* argo_profiles_errors.nc record 5 salinity level 11 flag 3 by density_inversion
* argo_profiles_errors.nc record 5 temperature level 11 flag 3 by density_inversion
* argo_profiles_errors.nc record 7 salinity level 56 flag 3 by density_inversion
* argo_profiles_errors.nc record 7 temperature level 55 flag 3 by density_inversion
* argo_profiles_errors.nc record 7 temperature level 56 flag 3 by density_inversion
* argo_profiles_errors.nc record 9 salinity level 21 flag 3 by density_inversion
* argo_profiles_errors.nc record 9 temperature level 20 flag 3 by density_inversion
* argo_profiles_errors.nc record 9 temperature level 21 flag 3 by density_inversion
* ctd_casts_errors.csv record 0 salinity level 29 flag 3 by density_inversion
* ctd_casts_errors.csv record 0 salinity level 30 flag 3 by density_inversion
* ctd_casts_errors.csv record 0 temperature level 29 flag 3 by density_inversion
* ctd_casts_errors.csv record 2 salinity level 151 flag 3 by density_inversion
* ctd_casts_errors.csv record 2 temperature level 150 flag 3 by density_inversion
* ctd_casts_errors.csv record 2 temperature level 151 flag 3 by density_inversion

## Clean raw files, flags 3/4

* argo_synthetic_arabian_sea_2025Q1.nc: 0
* bathymetry_synthetic_0p1deg.nc: 0
* buoy_met_wave_synthetic_2025Q1.csv: 0
* ctd_casts_synthetic_arabian_sea_2025.csv: 0
* sst_synthetic_0p25deg_2025Q1.nc: 0

Synthetic data, written by the same author as the tests. A good score shows the code does what its author intended on planted errors of the kinds chosen, not that it works on real observations.
