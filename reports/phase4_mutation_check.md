# Phase 4 mutation check

One safeguard is switched off at a time; the guarding tests must then fail.

| Safeguard switched off | Caught | First failing test |
|---|---|---|
| authorisation check | yes | tests/integration/test_access_api.py::test_role_matrix_every_role_against_every_endpoint |
| fail-closed on an unlisted route | yes | tests/integration/test_access_api.py::test_a_route_without_a_policy_row_is_refused |
| published-only input filter | yes | tests/integration/test_products_pipeline.py::test_only_published_data_is_ever_read[False-ingested] |
| good-flag filter | yes | tests/unit/test_products.py::test_flags_3_4_9_never_enter_a_product |
| stale marking (trigger) | yes | tests/integration/test_products_db.py::test_the_trigger_marks_products_stale_when_the_dataset_leaves_published |
| stale marking (view notices a new review) | yes | tests/integration/test_products_db.py::test_the_view_notices_a_new_review_version |
| query-log atomicity | yes | tests/integration/test_access_api.py::test_if_the_log_row_cannot_be_written_nothing_is_returned |
| row limit | yes | tests/integration/test_access_api.py::test_every_limit_is_enforced_once |
| CSV formula neutralisation | yes | tests/unit/test_access.py::test_csv_cells_that_look_like_formulas_are_neutralised_but_numbers_are_not |
| hostile-filter validation | yes | tests/unit/test_access.py::test_hostile_or_malformed_filters_are_refused[bbox=a,b,c,d] |
| lazy read (reads everything) | yes | tests/unit/test_access.py::test_a_small_window_reads_only_the_chunks_it_needs |
| determinism (a timestamp in the product) | yes | tests/integration/test_products_pipeline.py::test_the_same_inputs_and_code_give_the_same_checksums[binned_profile-tiny_profiles] |
| object checksum check (lazy store) | yes | tests/unit/test_access.py::test_a_changed_object_or_manifest_is_refused |
