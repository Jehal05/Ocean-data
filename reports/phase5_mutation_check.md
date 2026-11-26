# Phase 5 mutation check

One dashboard safeguard is switched off at a time; the guarding tests must then fail.

| Safeguard switched off | Caught | First failing test |
|---|---|---|
| API strings are inserted as text, not markup | yes | tests/integration/test_dashboard_browser.py::test_hostile_titles_and_platform_ids_are_text_and_run_nothing |
| CSRF header sent on every request | yes | tests/integration/test_dashboard_browser.py::test_logout_needs_the_csrf_header_and_really_ends_the_session |
| page Content-Security-Policy | yes | tests/integration/test_dashboard_browser.py::test_csp_is_enforced_on_the_pages_and_no_view_causes_a_violation |
| truncation notice | yes | node test: not ok 14 - notices: truncated and stale are said plainly |
| stale notice | yes | node test: not ok 14 - notices: truncated and stale are said plainly |
| a missing value is a gap, not a zero (line) | yes | node test: not ok 4 - gaps: a missing value ends a run, nothing is joined across it |
| flag shown with its label | yes | tests/integration/test_dashboard_browser.py::test_profile_with_all_values_shows_the_flag_by_shape_and_label |
| unknown flag is never shown as good | yes | node test: not ok 1 - flags: shape and label for every flag, unknown is never good |
| map box wraps at 180 degrees | yes | node test: not ok 7 - longitude wrap at 180 degrees |
| session expiry sends the user to login | yes | tests/integration/test_dashboard_browser.py::test_an_expired_session_sends_the_user_back_to_the_login_page |
| products are asked without the quality parameter | yes | tests/integration/test_dashboard_browser.py::test_a_stale_product_is_refused_with_the_reason_and_shown_with_a_notice_when_asked |
| only the newest request draws (map) | yes | tests/integration/test_dashboard_browser.py::test_an_older_answer_arriving_late_never_overwrites_the_newer_one |
