# Phase 3 mutation check

One safeguard is switched off at a time; the guarding tests must then fail.

| Safeguard switched off | Caught | First failing test |
|---|---|---|
| authorisation | yes | tests/integration/test_review_api.py::test_role_matrix_every_role_against_every_endpoint |
| fail-closed on unlisted routes | yes | tests/integration/test_review_api.py::test_a_route_without_a_policy_row_is_refused |
| comment required (API) | yes | tests/integration/test_review_api.py::test_a_comment_is_required[] |
| four-eyes | yes | tests/integration/test_review_api.py::test_four_eyes_on_blocks_the_ingester_and_off_allows_it |
| unreviewed flag-4 guard | yes | tests/integration/test_review_api.py::test_unreviewed_flag4_blocks_approval_unless_accepted_and_recorded |
| audit atomic with a flag change | yes | tests/integration/test_review_api.py::test_a_failing_audit_row_undoes_the_flag_change |
| audit atomic with a status change | yes | tests/integration/test_review_api.py::test_a_failing_audit_row_undoes_the_status_change |
| publish uses the approved snapshot | yes | tests/integration/test_review_api.py::test_publish_uses_exactly_the_approved_snapshot |
| approval takes the exclusive lock | yes | tests/integration/test_review_api.py::test_an_approval_waits_for_a_change_in_progress_and_includes_it |
| cookie CSRF header | yes | tests/integration/test_review_api.py::test_cookie_session_needs_the_csrf_header_on_changes |
| cleared rows after a config change | yes | tests/integration/test_review_api.py::test_a_cell_that_stops_being_flagged_is_cleared_and_overrides_still_win |
| verify uploaded objects | yes | tests/integration/test_review_api.py::test_a_corrupted_upload_is_caught_before_anything_is_published |
| human decision beats automatic (view) | yes | tests/integration/test_review_db.py::test_human_decision_beats_a_later_automatic_row |
| status transition guard (database) | yes | tests/integration/test_review_db.py::test_review_role_cannot_move_status_off_the_review_path |
| human-flag guard (database) | yes | tests/integration/test_review_db.py::test_human_flag_guard_refuses[kwargs0] |
| login rate limit (database) | yes | tests/integration/test_review_db.py::test_lock_after_failures_and_unlock_after_window |
| token expiry check (database) | yes | tests/integration/test_review_db.py::test_token_validity_rules |
