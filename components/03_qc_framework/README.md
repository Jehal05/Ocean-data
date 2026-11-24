# 03 QC Framework

**Phase 2: built.** Package `scats_qc/`.

* Ten automated tests (`tests.py`), one class each, ids and categories matching the `qc_test` table: `missing_value`, `format_consistency`, `global_range`, `regional_range`, `spike`, `position_on_land`, `position_range`, `timestamp_valid`, `timestamp_monotonic`, `density_inversion`. `expert_review` is Phase 3.
* `engine.run_qc(ds)` applies every test, combines flags worst-wins (order 0 < 1 < 2 < 5 < 8 < 9 < 3 < 4), turns cells that passed everything applied into 1, and returns the findings. Running it twice gives the same result.
* A hard test sets 4, a soft test sets 3. Each finding's `detail` holds the observed value, the threshold and the config version.
* `qc_config.yaml` is versioned (0.2.0: no threshold value changed since 0.1.0); every threshold has a rationale and a source, and the loader refuses a threshold without them. Changing a threshold after seeing results must be recorded in `docs/phase2_design.md` section 8 and in the config `history`.

## Limits

* The spike test cannot see the first and last record of a profile or series; they get range tests only.
* `timestamp_monotonic` cannot tell which record is wrong when two consecutive records are wrong or a pair is swapped: it flags both as ambiguous. A forward jump on the last record is invisible.
* `density_inversion` flags both levels of the offending pair, so a single bad value can earn soft flag 3 on its neighbours (collateral density flags: 21 around 11 culprit cells in the scoring run, `reports/phase2_qc_score.md`; reasoning in `docs/phase2_design.md` section 8 item 11).
* Units declared in a CSV header are converted; when a file declares none the mapping is trusted and `units_assumed_from_mapping` is true.
* Regional ranges and the buoy spike thresholds are engineering judgement; the Argo-derived thresholds were recalled from memory. All are open questions for NPOL.
* `position_on_land` has about 1 km resolution. `timestamp_valid` needs a `reference_time` (the ingest service passes one); without it only an absolute fallback date applies.
