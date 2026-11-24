# Phase 2 QC score (BLIND error set)

Database: local stand-in PostgreSQL 16 WITHOUT PostGIS (tests/db_stub.py)
QC config version: 0.2.0

**Recall: 47/47**   |   false positives in error files: 36   |   collateral density flags: 36 around 25 culprit cells   |   flags 3/4 on the five clean raw files: 0

| Error type | Detected | Total |
|---|---|---|
| density_inversion | 3 | 3 |
| missing | 5 | 5 |
| out_of_range | 9 | 9 |
| position_invalid | 2 | 2 |
| position_on_land | 2 | 2 |
| spike | 16 | 16 |
| timestamp_duplicate | 2 | 2 |
| timestamp_jump_forward | 2 | 2 |
| timestamp_non_monotonic | 6 | 6 |

| Magnitude band | Correct | Total |
|---|---|---|
| below_threshold | 11 | 11 |
| just_above | 17 | 17 |
| just_above_global | 9 | 9 |
| just_inside_global | 2 | 2 |
| n/a | 13 | 13 |
| swapped_pair | 4 | 4 |
| well_above | 2 | 2 |

Expected primary test fired on the cell (any finding, not only the winner): 47/47

## Misses

none

## Negative controls flagged (should have been left alone)

none

## False positives (flag 3/4 on a cell the key does not explain)

* argo_blind_errors.nc record 0 salinity level 7 flag 3 by density_inversion
* argo_blind_errors.nc record 0 salinity level 8 flag 3 by density_inversion
* argo_blind_errors.nc record 0 temperature level 7 flag 3 by density_inversion
* argo_blind_errors.nc record 1 salinity level 69 flag 3 by density_inversion
* argo_blind_errors.nc record 1 salinity level 70 flag 3 by density_inversion
* argo_blind_errors.nc record 1 temperature level 69 flag 3 by density_inversion
* argo_blind_errors.nc record 2 salinity level 13 flag 3 by density_inversion
* argo_blind_errors.nc record 2 temperature level 12 flag 3 by density_inversion
* argo_blind_errors.nc record 2 temperature level 13 flag 3 by density_inversion
* argo_blind_errors.nc record 3 salinity level 67 flag 3 by density_inversion
* argo_blind_errors.nc record 3 temperature level 66 flag 3 by density_inversion
* argo_blind_errors.nc record 3 temperature level 67 flag 3 by density_inversion
* argo_blind_errors.nc record 5 salinity level 29 flag 3 by density_inversion
* argo_blind_errors.nc record 5 salinity level 30 flag 3 by density_inversion
* argo_blind_errors.nc record 5 temperature level 29 flag 3 by density_inversion
* argo_blind_errors.nc record 6 salinity level 41 flag 3 by density_inversion
* argo_blind_errors.nc record 6 temperature level 40 flag 3 by density_inversion
* argo_blind_errors.nc record 6 temperature level 41 flag 3 by density_inversion
* argo_blind_errors.nc record 8 salinity level 50 flag 3 by density_inversion
* argo_blind_errors.nc record 8 salinity level 51 flag 3 by density_inversion
* argo_blind_errors.nc record 8 temperature level 51 flag 3 by density_inversion
* argo_blind_errors.nc record 9 salinity level 26 flag 3 by density_inversion
* argo_blind_errors.nc record 9 temperature level 25 flag 3 by density_inversion
* argo_blind_errors.nc record 9 temperature level 26 flag 3 by density_inversion
* ctd_blind_errors.csv record 0 salinity level 59 flag 3 by density_inversion

## Clean raw files, flags 3/4

* argo_synthetic_arabian_sea_2025Q1.nc: 0
* bathymetry_synthetic_0p1deg.nc: 0
* buoy_met_wave_synthetic_2025Q1.csv: 0
* ctd_casts_synthetic_arabian_sea_2025.csv: 0
* sst_synthetic_0p25deg_2025Q1.nc: 0

Synthetic data, written by the same author as the tests. A good score shows the code does what its author intended on planted errors of the kinds chosen, not that it works on real observations.
