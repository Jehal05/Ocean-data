# Phase 5 mutation check

One dashboard safeguard is switched off at a time; the guarding tests must then fail.

| Safeguard switched off | Caught | First failing test |
|---|---|---|
| API strings are inserted as text, not markup | yes | tests/integration/test_dashboard_browser.py::test_hostile_titles_and_platform_ids_are_text_and_run_nothing |
| CSRF header sent on every request | yes | tests/integration/test_dashboard_browser.py::test_logout_needs_the_csrf_header_and_really_ends_the_session |
| page Content-Security-Policy | yes | tests/integration/test_dashboard_browser.py::test_csp_is_enforced_on_the_pages_and_no_view_causes_a_violation |
| truncation notice | yes | node test: not ok 13 - notices: truncated and stale are said plainly |
| stale notice | yes | node test: not ok 13 - notices: truncated and stale are said plainly |
| a missing value is a gap, not a zero (line) | yes | node test: not ok 4 - gaps: a missing value ends a run, nothing is joined across it |
| flag shown with its label | yes | tests/integration/test_dashboard_browser.py::test_profile_with_all_values_shows_the_flag_by_shape_and_label |
| unknown flag is never shown as good | yes | node test: not ok 1 - flags: shape and label for every flag, unknown is never good |
| footprint that crosses 180 is drawn in two pieces | yes | node test: not ok 7 - longitude wrap at 180 degrees |
| box that crosses 180 is drawn in two pieces | yes | tests/integration/test_dashboard_browser.py::test_a_footprint_and_a_box_that_cross_180_degrees_are_each_drawn_in_two_pieces |
| session expiry sends the user to login | yes | tests/integration/test_dashboard_browser.py::test_an_expired_session_sends_the_user_back_to_the_login_page |
| products are asked without the quality parameter | yes | tests/integration/test_dashboard_browser.py::test_a_stale_product_is_refused_with_the_reason_and_shown_with_a_notice_when_asked |
| only the newest request draws (map) | yes | tests/integration/test_dashboard_browser.py::test_an_older_answer_arriving_late_never_overwrites_the_newer_one |
| /health: only a loopback caller is told about default passwords | yes | tests/integration/test_access_api.py::test_only_a_loopback_caller_is_told_about_default_passwords_and_a_forwarded_header_changes_nothing |
| /health: a relayed request (forwarding header) is not the console | yes | tests/integration/test_access_api.py::test_only_a_loopback_caller_is_told_about_default_passwords_and_a_forwarded_header_changes_nothing |
| /health: the default-password answer is cached | yes | tests/integration/test_access_api.py::test_default_password_answer_is_cached_and_a_failure_is_not |
| /health: a failed lookup is not cached | yes | tests/integration/test_access_api.py::test_default_password_answer_is_cached_and_a_failure_is_not |
| login page shows no warning unless the server says true | yes | tests/integration/test_dashboard_browser.py::test_login_page_shows_no_password_warning_when_the_server_says_null_or_false |
| an export nobody imports is dead code | yes | tests/unit/test_dashboard_static.py::test_every_export_of_logic_and_svg_is_imported_by_another_app_file |
| SST row cap | yes | tests/integration/test_dashboard_browser.py::test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages |
| SST partial-draw notice | yes | tests/integration/test_dashboard_browser.py::test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages |
| series row cap | yes | tests/integration/test_dashboard_browser.py::test_series_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages |
| series partial-draw notice | yes | tests/integration/test_dashboard_browser.py::test_series_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages |
| focus outline colour | yes | tests/unit/test_dashboard_contrast.py::test_chart_marks_and_the_focus_outline_have_at_least_3_to_1 |
| band outline | yes | tests/unit/test_dashboard_contrast.py::test_the_check_can_fail_the_colours_it_replaced_were_below_the_limit |
| link target size in the map table (axe) | yes | tests/integration/test_dashboard_browser.py::test_axe_core_finds_no_wcag_a_or_aa_violation_in_any_view |
| test helper `until` fails on a real page error | yes | tests/integration/test_dashboard_browser.py::test_until_retries_a_navigation_but_nothing_else |
| a footprint that crosses 180 is matched by an overlapping box | yes | tests/integration/test_access_api.py::test_a_footprint_that_crosses_180_is_found_by_every_box_that_overlaps_it |
| a plain footprint is matched by a box that crosses 180 | yes | tests/integration/test_access_api.py::test_a_plain_footprint_is_found_by_a_box_that_crosses_180_exactly_as_before |
| SST cells have an outline | yes | tests/unit/test_dashboard_contrast.py::test_every_end_of_the_sst_colour_scale_is_seen_against_the_page |
| SST colour bar has a frame | yes | tests/integration/test_dashboard_browser.py::test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages |
