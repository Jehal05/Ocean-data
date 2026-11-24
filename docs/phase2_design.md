# Phase 2 design note: parser API and QC framework

Status: written BEFORE any Phase 2 code, as agreed. Later changes are listed in section 8.

## 0. Layout

| Path | Contents |
|---|---|
| `libs/scats_common/` | Shared package: `data_model.yaml` (machine-readable copy of `docs/data_model.md`), unit converters, flag scheme, `validate_dataset()` |
| `components/02_parser_api/scats_parsers/` | Parser framework, five parsers, YAML mappings |
| `components/02_parser_api/scats_ingest/` | Ingest service: staging files, database writes, flag-consistency check |
| `components/02_parser_api/app/` | FastAPI service |
| `components/03_qc_framework/scats_qc/` | QC engine, the 10 automated tests, versioned config |
| `tests/` | Unit tests, `score_qc.py`, `check_phase2.py` |

Import paths are set in `pytest.ini` and in the API `Dockerfile` (`PYTHONPATH`). Nothing is pip-installed.

## 1. Parser plugin interface

```python
class Parser:                       # one subclass per file format
    name = "argo_netcdf"
    mapping_file = "argo_netcdf.yaml"
    @classmethod
    def can_parse(cls, path) -> bool          # cheap sniff: extension plus content (variables / header)
    def parse(self, path) -> xr.Dataset       # returns the common data model; raises ParseError, never crashes
```

* `registry.detect(path)` returns the first parser whose `can_parse` is true; `registry.parse_file(path)` detects and parses.
* **Adding a format**: write one class, add one YAML mapping, decorate with `@register`. Nothing else changes.
* Names, units and fill values live in the YAML mapping, not in code. NPOL can change a mapping without touching Python.

## 2. What an `observation` is

| Source | One observation is | `source_record` | `platform_id` | Staging dims |
|---|---|---|---|---|
| Argo NetCDF | one profile | 0-based profile index | WMO number | `profile`, `level` |
| Ship CTD CSV | one cast | 0-based cast ordinal | cast id | `profile`, `level` |
| Buoy CSV | one record (row) | 0-based data row | buoy id | `record` |
| SST grid | one time slice | 0-based time index | none | `time`, `latitude`, `longitude` |
| Bathymetry grid | the whole grid | 0 | none | `latitude`, `longitude` |

Extra staging variable `platform_group` (Argo: float, CTD: cruise, buoy: buoy) is what `timestamp_monotonic` groups by, because a CTD cast is its own `platform_id`.

Dataset variable names are the **common names** from `docs/data_model.md` (`temperature`, `salinity`, ...). The CF name is the `standard_name` attribute. Flag variables are `<name>_qc`. Positions and times have flag variables `position_qc` and `time_qc`.

## 3. Flag persistence (resolves architecture.md section 5)

* **Staging dataset** (NetCDF in a configurable directory; Phase 3 migrates to Zarr) holds the full flag arrays for every cell. Nothing is written to the `standardised` bucket: that is for approved data only.
* **PostgreSQL `qc_flag`** stores every flag that is not 0 or 1, that is 2, 3, 4, 5, 8 and 9, one row per observation, variable and level. `observation.overall_flag` holds the worst flag per observation.
* **Grids** (SST, bathymetry) are the exception, because a per-cell table would be huge and land-masked cells are structural, not errors: PostgreSQL gets one summary row per time slice and variable for flags 3 and 4 only, with the cell count in `detail`. Structurally missing cells stay in staging only.
* `scats_ingest.consistency.verify_flag_consistency()` recomputes what PostgreSQL should contain from the staging file and reports any difference.
* Position and time use variable names `position` and `time`, `level_index = -1`. A whole-variable flag also uses `-1`.

## 4. Flag combination

* Severity order, low to high: `0 < 1 < 2 < 5 < 8 < 9 < 3 < 4`. The worst flag wins. 9 (missing) is final in practice, because every test skips missing values.
* The parser sets the initial flag: 9 where the value is missing, the mapped provider flag where the source has one (Argo `*_QC`), otherwise 0.
* A hard test (`qc_test.severity = hard`) sets 4, a soft test sets 3.
* After all tests, a cell that is still 0 and was covered by at least one test becomes 1 (good = "passed every test applied").
* A provider flag worse than what our tests find is kept (worst wins).

## 5. Scoring rules (fixed before QC code)

A key row from `answer_key.csv` is **detected** when a persisted flag of 3, 4 or 9 (current decision, `qc_flag_current`) exists on a matching cell:

| Error type | Matching cell |
|---|---|
| spike, out_of_range, missing | exact: same file, record, level, variable |
| position_on_land, position_invalid | variable `position`, same record |
| timestamp_duplicate | variable `time`, same record or record - 1 |
| timestamp_non_monotonic | variable `time`, same record |
| density_inversion | temperature, salinity or density, level L or L - 1, same record |

* Ship CTD rows match by (`cast_id`, `level_index`). Source variable names become common names through the parser's own YAML mapping (the scorer imports it; there is no second copy).
* Extra flags from other tests on a key cell are allowed.
* A **false positive** is a flag of 3 or 4 on a cell that no key row matches in the errors files, or any 3 or 4 flag on the five clean raw files.
* The QC code never reads `answer_key.csv` and nothing in it knows how errors were injected.

## 6. QC method notes

| Test | Method | Source |
|---|---|---|
| global_range | `valid_min`/`valid_max` of the data model | `docs/data_model.md` section 3 |
| regional_range | Config ranges for the Arabian Sea and northern Indian Ocean, flag 3 | Engineering judgement, see config rationale. Open question for NPOL. |
| spike (profiles T, S) | `abs(V2 - (V1+V3)/2) - abs((V3-V1)/2)` against 6.0 degC / 0.9 psu above 500 dbar, 2.0 degC / 0.3 psu at and below | Argo Quality Control Manual, test 9 (recalled, not re-fetched; verify the numbers) |
| spike (time series) | Same formula, per-variable thresholds | Engineering judgement, see config rationale |
| density_inversion | Density from TEOS-10 at the mid pressure of each level pair; flag if it drops by more than 0.03 kg m-3 | Argo Quality Control Manual, test 14 (recalled, verify). Tolerance is open question 6 for NPOL |
| position_on_land | `global_land_mask`, about 1 km resolution | Limit: coastal points within roughly a kilometre of the coast can be misclassified |
| timestamp_monotonic | A record must be later than the previous record of its `platform_group`. Duplicates and decreases flag the later record | none |

Thresholds are never tuned to the test data. If one has to change after seeing results, the reason goes in section 8.

## 7. Known limits of this phase

* Grids get range, missing, format and time tests only; no spike or land test.
* Per-profile pressure/depth conversion clips latitude to [-90, 90] and treats a missing latitude as 0, so a bad position does not break the density test. The error is a few metres at most.
* No manual review (`expert_review`); that is Phase 3.

## 8. Changes made after the design was written

1. **Phase 1 data bug found by `timestamp_monotonic`.** `make_synthetic_data.py` built float ids `SYN2900000`.. (10 characters) but stored `PLATFORM_NUMBER` as 8-byte strings, so all six floats became `SYN29000` and their interleaved cycles looked like time running backwards (5 flag-4 results on the clean Argo file). The test was right; the data was wrong. Ids are now `SYN29000`..`SYN29005`, the generator, error file and `MANIFEST.sha256` were regenerated. Only the two Argo files and the manifest changed; the answer key is unchanged. No QC threshold was touched.
2. **`density_inversion` flags both levels of the offending pair** (temperature and salinity), because the test cannot tell which level is wrong. Decided while writing the test, before any scoring existed. The scoring rule in section 5 is widened to match: a flag from `density_inversion` on level L-1, L or L+1 of a density key row is an *associated* flag, not a false positive. Flags from any other test on those cells still count as false positives.
3. **`provider_flag` test id.** Flags that come from the data provider (Argo `*_QC`) have no QC test of ours. Migration `004` adds `provider_flag` to `qc_test` so every persisted flag can name its origin.
