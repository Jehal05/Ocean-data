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
* **Limits of the tests (review round).** The spike test cannot see the first and last record of a profile or series; they get range tests only.
* `timestamp_monotonic` cannot tell which record is wrong when two consecutive records are wrong or a pair is swapped: it flags both as ambiguous. A forward jump on the last record is invisible.
* `density_inversion` flags both levels of the offending pair, so a single bad value can earn soft flag 3 on its neighbours (collateral density flags, section 8 item 11).
* Units declared in a CSV header are converted; when a file declares none the mapping is trusted and `units_assumed_from_mapping` is true.
* Regional ranges and the buoy spike thresholds are engineering judgement; the Argo-derived thresholds were recalled from memory. All are open questions for NPOL.

## 8. Changes made after the design was written

1. **Phase 1 data bug found by `timestamp_monotonic`.** `make_synthetic_data.py` built float ids `SYN2900000`.. (10 characters) but stored `PLATFORM_NUMBER` as 8-byte strings, so all six floats became `SYN29000` and their interleaved cycles looked like time running backwards (5 flag-4 results on the clean Argo file). The test was right; the data was wrong. Ids are now `SYN29000`..`SYN29005`, the generator, error file and `MANIFEST.sha256` were regenerated. Only the two Argo files and the manifest changed; the answer key is unchanged. No QC threshold was touched.
2. **`density_inversion` flags both levels of the offending pair** (temperature and salinity), because the test cannot tell which level is wrong. Decided while writing the test, before any scoring existed. The scoring rule in section 5 is widened to match: a flag from `density_inversion` on level L-1, L or L+1 of a density key row is an *associated* flag, not a false positive. Flags from any other test on those cells still count as false positives.
3. **`provider_flag` test id.** Flags that come from the data provider (Argo `*_QC`) have no QC test of ours. Migration `004` adds `provider_flag` to `qc_test` so every persisted flag can name its origin.

### Review round (changes after the first Phase 2 commit, cbeed03)

Written BEFORE the score was re-run. Honest note on order: the code for items 4 to 8 was drafted first and this text written straight after, before any score run. The regression tests were written, and shown failing, before any fix.

4. **Staging written after the commit.** `ingest_file` wrote the staging file before the database transaction, so a rejected re-ingest (same name, different content) overwrote the staging file of the dataset already in the database. Now: temp file, database commit, atomic rename. A failure before the commit deletes the temp file. If the rename fails after the commit, `IngestError` says the database is ahead and `verify_flag_consistency` reports the missing staging file.
5. **`timestamp_monotonic` identifies the wrong record.** Old rule: flag the later record of every out-of-order adjacent pair, so a forward jump blamed the record after it. New rule: for an out-of-order pair (p, i) look at the outer neighbours pp and n. A record is consistent if it lies strictly between them. Only i inconsistent: flag i (backward step). Only p inconsistent: flag p (forward jump). Equal times: flag the later one (duplicate). Both or neither consistent: flag both, `detail.ambiguous = true` (swapped pair, two consecutive wrong records). Invisible by construction: a forward jump on the last record, a first record that is wrong but still earlier than the second. A backward step on the last record is ambiguous, because there is no successor to decide. The scoring rules of section 5 stay as written (duplicate: record or record - 1; non-monotonic: same record) and still apply to ambiguous flags because both records are flagged. An older unit test that expected a swapped pair to be resolved was changed to expect ambiguity, with a comment.
6. **`is_synthetic` is declared.** The text search for the word SYNTHETIC is gone. CSV header `# synthetic: true|false`, NetCDF global attribute `is_synthetic`. Absent gives `unknown` (attribute and API); the database column stays `NOT NULL DEFAULT FALSE`, so an unknown file is stored as FALSE and `dataset.attributes.is_synthetic = "unknown"` says so; `/qc-summary` reports `synthetic_status`. A malformed value is a `ParseError`. The generator now writes the declaration; all raw and error files, and both manifests, were regenerated; the answer key is unchanged. (The test-errors manifest had also been stale since the previous regeneration; fixed.)
7. **Units.** A CSV may declare `# units: col=unit, col2=unit2` (comma separated; the earlier space-separated header of the CTD file is replaced and the parser rejects that form). A declared unit that differs from the mapping is converted; one that cannot be converted is a `ParseError` naming the column. Undeclared mapped columns set `units_assumed_from_mapping = true` (also for NetCDF variables that lack a `units` attribute).
8. **Determinism.** `timestamp_valid` no longer reads the clock. Chosen: option (b), `run_qc(..., reference_time=...)`, stored as `qc_reference_time`. Reason: a future-date check is only meaningful against the processing time, so a fixed calendar date (option a) would either miss real future dates now or start flagging real data later. The ingest service passes the time of the first ingest and reuses it for re-ingests of the same dataset, so re-processing never changes a result. Without a reference time only an absolute fallback (2100-01-01) applies, which is weak and documented as such. qc_config.yaml version 0.2.0; no threshold value changed.
9. **Grants.** `db/init/005_ingest_grants_tighten.sql` (004 untouched): column-level UPDATE, a trigger that refuses any dataset status other than ingested / qc_running / qc_done from `scats_ingest`.
10. **API access.** An empty `INGEST_API_KEY` disables `POST /ingest` (503); `INGEST_ALLOW_ANONYMOUS=true` is the explicit development switch. `qc-summary` needs the key whenever one is set (with no key set it stays readable, read-only, localhost only). `/health` and `/parsers` are open.
11. **Collateral density flags: left as they are, documented.** Measured from the persisted flags only (no answer key): 21 soft flag-3 `density_inversion` flags sit within one level of 11 cells that a spike, global-range or regional-range test had already flagged, about 1.9 per culprit cell. Option (b), blaming the level that departs most from the mean of its outer neighbours, was rejected for three reasons that do not involve recall: (1) it needs an "inside the noise, cannot decide" cut-off and a normalisation between temperature and salinity, constants that have no source and would have to be invented; (2) a real density inversion without any outlier (a genuine instability, or a bad cast) has no culprit, and flagging both levels is the conservative answer the Argo test gives; (3) the collateral flags are soft (3), they never change the flag of the culprit cell (worst wins), and each one names `density_inversion` and the pair of levels in its detail, so a reviewer can see why. The cost is reviewer attention, stated in the QC README and the qc-summary `limits`. If NPOL wants fewer, that is a requirement to agree, not a number for me to guess.

