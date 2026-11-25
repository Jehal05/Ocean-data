# SCATS Common Data Model: DRAFT v0.1

**Status: DRAFT proposal.** In the real project NPOL defines and maintains this
model. Everything below is a starting point for NPOL to confirm or change.
Section 8 lists the questions that need an NPOL answer.

**Verify before relying on it:** the CF standard names below were written from
knowledge of the CF table, without access to the current published table. Check
each one against the latest CF Standard Name Table before Phase 2 code depends on it.

## 1. Principles

1. One internal representation: an xarray/CF-style dataset with named dimensions, coordinates and per-variable attributes.
2. Standard names follow CF Conventions wherever a standard name exists.
3. Canonical units are fixed per variable. Parsers convert on the way in and record the original units.
4. Every data variable has an ancillary quality-flag variable using one flag scheme (section 6).
5. Raw files are kept untouched. Nothing in the standardised zone is changed without an audit entry.
6. Time is UTC. Longitude is -180 to 180. Depth is positive down. Latitude is -90 to 90.

## 2. Coordinates

| Name | CF standard name | Units | Convention |
|---|---|---|---|
| `time` | `time` | `seconds since 1970-01-01T00:00:00Z` | UTC, ISO 8601 on input |
| `latitude` | `latitude` | `degrees_north` | -90 to 90, WGS84 |
| `longitude` | `longitude` | `degrees_east` | -180 to 180 (convert 0-360 input) |
| `depth` | `depth` | `m` | positive down, from sea surface |
| `pressure` | `sea_water_pressure` | `dbar` | gauge pressure, 0 at the surface |

## 3. Standard variables

"Proposed hard range" is the limit used by the `global_range` QC test (flag 4 outside it). These ranges are proposals to be tuned with oceanographers.

| Common name | CF standard name | Canonical units | Proposed hard range | Notes |
|---|---|---|---|---|
| `temperature` | `sea_water_temperature` | degC | -2.5 to 40 | In situ, ITS-90 |
| `salinity` | `sea_water_practical_salinity` | 1 (PSS-78) | 2 to 42 | Practical salinity |
| `sound_velocity` | `speed_of_sound_in_sea_water` | m s-1 | 1400 to 1700 | Measured or derived (TEOS-10) |
| `density` | `sea_water_sigma_theta` | kg m-3 | 0 to 40 | Derived from T, S, p |
| `current_speed` | `sea_water_speed` | m s-1 | 0 to 5 | Source often cm/s: convert |
| `current_direction` | `direction_of_sea_water_velocity` | degree | 0 to 360 | Direction the water flows TOWARD, clockwise from north |
| `wind_speed` | `wind_speed` | m s-1 | 0 to 75 | Source often knots: convert |
| `wind_direction` | `wind_from_direction` | degree | 0 to 360 | Direction wind blows FROM |
| `relative_humidity` | `relative_humidity` | % | 0 to 100 | |
| `air_temperature` | `air_temperature` | degC | -50 to 60 | |
| `air_pressure` | `air_pressure_at_mean_sea_level` | hPa | 870 to 1085 | |
| `wave_height` | `sea_surface_wave_significant_height` | m | 0 to 30 | Hs |
| `wave_direction` | `sea_surface_wave_from_direction` | degree | 0 to 360 | Direction waves come FROM |
| `wave_period` | `sea_surface_wave_period_at_variance_spectral_density_maximum` | s | 0 to 30 | Peak period (Tp) |
| `bathymetry` | `sea_floor_depth_below_geoid` | m | 0 to 11000 | Positive down. GEBCO "elevation" is negative below sea level: flip the sign |
| `sea_level_anomaly` | `sea_surface_height_above_sea_level` | m | -3 to 3 | Anomaly product; set `long_name` to say so |
| `sea_surface_temperature` | `sea_surface_temperature` | degC | -2.5 to 40 | Satellite sources often in kelvin: convert |

Direction conventions differ between variables on purpose, as they do in CF and in the instruments. The parser must record which convention the source used and convert.

## 4. Controlled vocabularies

| Vocabulary | Allowed values |
|---|---|
| `platform_type` | `argo_float`, `ship_ctd`, `moored_buoy`, `satellite`, `numerical_model`, `bathymetry_grid` |
| `dataset_type` | `profile`, `timeseries`, `grid`, `bathymetry` |
| `data_mode` | `real_time`, `delayed_mode`, `reanalysis`, `synthetic` |
| `processing_level` | `raw`, `standardised`, `derived` |
| `dataset_status` | `ingested`, `qc_running`, `qc_done`, `in_review`, `approved`, `rejected`, `published` |
| `qc_category` | `format`, `missing`, `range`, `position`, `timestamp`, `instrument`, `cross_variable`, `expert` |
| `qc_severity` | `hard`, `soft` |
| `user_role` | `viewer`, `reviewer`, `admin` |

New values are added by an admin and recorded in the audit log.

## 5. Required metadata

### Dataset level (global attributes and `dataset` table)

| Attribute | Required | Meaning |
|---|---|---|
| `title`, `summary` | Yes | What the dataset is |
| `Conventions` | Yes | `CF-1.10` plus `SCATS-0.1` |
| `data_model_version` | Yes | Version of this document, e.g. `0.1` |
| `source`, `institution` | Yes | Provider |
| `platform_type`, `platform_id` | Yes | From the vocabulary, plus the float/buoy/cast id |
| `instrument` | If known | Sensor or product name |
| `data_mode` | Yes | From the vocabulary |
| `is_synthetic` | Yes | `true` for generated test data, so it can never be mistaken for observation. Always an explicit declaration by the data provider (`# synthetic: true` in a CSV header, global attribute `is_synthetic` in NetCDF); `unknown` when the file does not say. It is never guessed from the text of the file |
| `license` | Yes | Terms of use |
| `time_coverage_start`, `time_coverage_end` | Yes | ISO 8601 UTC |
| `geospatial_lat_min/max`, `geospatial_lon_min/max` | Yes | Bounding box |
| `geospatial_vertical_min/max` | Profiles | Depth range in m |
| `processing_level` | Yes | From the vocabulary |
| `original_file_name`, `original_checksum_sha256` | Yes | Ties the dataset to its untouched raw file |
| `date_created`, `history` | Yes | What was done, when, by which parser version |

### Variable level

| Attribute | Required | Meaning |
|---|---|---|
| `standard_name`, `long_name`, `units` | Yes | Canonical, from section 3 |
| `source_name`, `source_units` | Yes | Name and units in the original file |
| `valid_min`, `valid_max` | Yes | The hard range used by QC |
| `_FillValue` | Yes | Missing-value marker |
| `ancillary_variables` | Yes | Name of the matching flag variable |
| `coordinates` | Yes | Coordinates the variable depends on |
| `cell_methods` | If applicable | For example `time: mean` |
| `sensor` | If known | Sensor that produced it |

## 6. Quality flag scheme

Follows the Argo / IODE scheme, so Argo flags map directly. Flag variables use `flag_values` and `flag_meanings` attributes (CF).

| Flag | Meaning | Use in products |
|---|---|---|
| 0 | No QC applied | Excluded by default |
| 1 | Good | Included |
| 2 | Probably good | Included |
| 3 | Probably bad (may be correctable) | Excluded by default |
| 4 | Bad | Excluded |
| 5 | Value changed by expert reviewer | Included |
| 8 | Estimated / interpolated | Included, marked |
| 9 | Missing | Excluded |

Rules:

* A flag only moves by adding a new record. Old records are kept, so the history of decisions is complete (`qc_flag` table, `qc_flag_current` view).
* Every record names the test that set it (`qc_test` table) and, when a person set it, who.
* The worst flag among tests wins for automatic decisions. A human override always supersedes.
* Flags are held per observation, per variable and, for profiles, per level.

## 7. Zarr layout (target for Phase 3)

```
<zone>/<platform_type>/<dataset_key>.zarr
    coords:  time, latitude, longitude, depth/pressure
    data:    one array per standard variable
    flags:   <variable>_qc, one per data variable
    attrs:   dataset-level metadata from section 5
```

### Derived zone (Phase 4)

```
derived/<kind>/<dataset_key>/v<N>.zarr/       kind = binned_profile | series_hourly | series_daily | sst_monthly_mean
    <variable>, <variable>_n_obs (counts), plus _mean/_min/_max/_count for series; scats_manifest.json written last
    group attrs: product_kind, source_dataset_key, flags_used, processing_level = derived, scats_lineage (JSON)
```
Format 2, Blosc/zstd. N counts up; an old version stays in the store and in `derived_product` with `superseded_at`.

Lineage record (sorted JSON, also stored in `derived_product.lineage`; contains no timestamp except the source's `published_at`):
```
{"kind", "product_key", "version", "code_version", "code_sha256", "config_sha256",
 "source": {"dataset_key", "dataset_id", "store_uri", "store_manifest_sha256", "store_content_sha256",
            "review_id", "review_version", "qc_config_version", "qc_config_sha256", "published_at"},
 "flags_used": [1, 2], "parameters": {...}}
```
`qc_config_sha256` is filled only when the shipped QC config has the same version as the one the dataset was checked with, otherwise it is null.

## 8. Open questions for NPOL

1. Which instrument formats must be supported first, and can sample files be shared?
2. Is the CF standard-name list above acceptable, or does NPOL have its own vocabulary?
3. Should temperature be stored as in situ, conservative (TEOS-10) or both?
4. Which flag scheme does NPOL use? Is the Argo / IODE scheme acceptable?
5. What are the regional range limits for the area of interest? The hard ranges above are global.
6. What density-inversion tolerance should count as an error (a typical choice is a few hundredths of kg m-3)?
7. Which acoustic parameters are required in the first release (sonic layer depth, channel axis, surface duct, convergence zones)? Is propagation modelling in scope, or only parameters?
8. Which reference datasets define "land" and "EEZ boundaries" for position checks and the map viewer?
9. What are the confidentiality classes of the data? The tender requires a non-disclosure agreement, so some data may be restricted.
10. Which users and roles are needed, and does NPOL require integration with its own login system?
11. What are the expected data volumes and the retention period for the archive?
12. Is deployment on NPOL premises (air-gapped?) or in a cloud, and what hardware is available?
13. Should depth be positive down everywhere, or do some NPOL products use height/elevation?
14. Which datasets need real-time ingestion, and at what frequency?
15. What is the structure of the sediment database named in the scope of work (attributes, units, spatial form)? It is not modelled yet.

## 9. Assumptions made in this draft

* NPOL instrument formats are unknown. The Phase 1 sample data uses public product styles: Argo NetCDF, a ship CTD CSV, a buoy time-series CSV, GHRSST-style gridded NetCDF and GEBCO-style bathymetry.
* All ranges are generic, not tuned to NPOL's operating area.
* The 17 variables in section 3 cover the scope of work, plus air pressure (needed by the buoy data). The sediment database named in the scope is NOT modelled yet: its structure depends on NPOL's data (open question to add).
