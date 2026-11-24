# 03 QC Framework

**Phase 2: built.** Package `scats_qc/`.

* Ten automated tests (`tests.py`), one class each, ids and categories matching the `qc_test` table: `missing_value`, `format_consistency`, `global_range`, `regional_range`, `spike`, `position_on_land`, `position_range`, `timestamp_valid`, `timestamp_monotonic`, `density_inversion`. `expert_review` is Phase 3.
* `engine.run_qc(ds)` applies every test, combines flags worst-wins (order 0 < 1 < 2 < 5 < 8 < 9 < 3 < 4), turns cells that passed everything applied into 1, and returns the findings. Running it twice gives the same result.
* A hard test sets 4, a soft test sets 3. Each finding's `detail` holds the observed value, the threshold and the config version.
* `qc_config.yaml` is versioned; every threshold has a rationale and a source, and the loader refuses a threshold without them. Changing a threshold after seeing results must be recorded in `docs/phase2_design.md` section 8 and in the config `history`.

Known limits: the Argo-derived spike and density thresholds were recalled from memory, not re-checked; regional ranges are engineering judgement for the Arabian Sea and Bay of Bengal, an open question for NPOL; `position_on_land` has about 1 km resolution; `density_inversion` flags both levels of the offending pair, so a planted spike also earns soft flag 3 on its neighbours (18 such collateral flags in the scoring run, listed in `reports/phase2_qc_score.md`).
