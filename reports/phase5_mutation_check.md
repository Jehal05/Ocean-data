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
| band outline | yes | tests/unit/test_dashboard_contrast.py::test_the_check_can_fail_the_colours_it_replaced_were_below_the_limit |
| link target size in the map table  | yes | tests/integration/test_dashboard_browser.py::test_links_in_the_map_table_are_at_least_24_px_square |
| test helper `until` fails on a real page error | yes | tests/integration/test_dashboard_browser.py::test_until_retries_a_navigation_but_nothing_else |
| a footprint that crosses 180 is matched by an overlapping box | yes | tests/integration/test_access_api.py::test_a_footprint_that_crosses_180_is_found_by_every_box_that_overlaps_it |
| a plain footprint is matched by a box that crosses 180 | yes | tests/integration/test_access_api.py::test_a_plain_footprint_is_found_by_a_box_that_crosses_180_exactly_as_before |
| SST cells have an outline | yes | tests/unit/test_dashboard_contrast.py::test_every_end_of_the_sst_colour_scale_is_seen_against_the_page[light] |
| SST colour bar has a frame | yes | tests/integration/test_dashboard_browser.py::test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages |
| a flag chip carries the marker shape, not only words | yes | tests/integration/test_dashboard_browser.py::test_flag_chips_carry_the_marker_shape_and_the_words_in_the_legend_the_table_and_the_strip |
| table flag cells are chips | yes | tests/integration/test_dashboard_browser.py::test_flag_chips_carry_the_marker_shape_and_the_words_in_the_legend_the_table_and_the_strip |
| the legend names all six flags | yes | tests/integration/test_dashboard_browser.py::test_flag_chips_carry_the_marker_shape_and_the_words_in_the_legend_the_table_and_the_strip |
| the provenance strip shows the whole checksum in its title | yes | tests/integration/test_dashboard_browser.py::test_every_figure_has_a_provenance_strip_with_versions_a_short_checksum_and_flag_chips |
| a dataset's flags used are shown as chips | yes | tests/integration/test_dashboard_browser.py::test_every_figure_has_a_provenance_strip_with_versions_a_short_checksum_and_flag_chips |
| 'Copied' is announced as a status | yes | tests/integration/test_dashboard_browser.py::test_the_copy_buttons_copy_the_checksum_and_a_one_line_caption_and_say_so |
| a refused clipboard falls back to a selected text field | yes | tests/integration/test_dashboard_browser.py::test_when_the_clipboard_is_refused_the_text_is_shown_selected_in_a_visible_field |
| the caption says when the data is synthetic | yes | node test: not ok 23 - the figure caption line names the view, the key, the versions and the flags, and says when the data is synthetic |
| Escape closes the map card | yes | tests/integration/test_dashboard_browser.py::test_a_footprint_card_opens_on_hover_and_on_focus_enter_pins_it_and_escape_closes_it |
| the map has a degree graticule every 30 degrees | yes | tests/integration/test_dashboard_browser.py::test_the_map_has_an_ocean_a_degree_graticule_and_a_legend_by_type |
| a grid footprint is dotted (a pattern, not only a colour) | yes | tests/integration/test_dashboard_browser.py::test_the_map_has_an_ocean_a_degree_graticule_and_a_legend_by_type |
| the profile crosshair highlights the table row | yes | tests/integration/test_dashboard_browser.py::test_the_profile_crosshair_readout_and_table_row_follow_the_pointer_and_the_arrow_keys |
| the profile readout carries the flag chip | yes | tests/integration/test_dashboard_browser.py::test_the_profile_crosshair_readout_and_table_row_follow_the_pointer_and_the_arrow_keys |
| the series brush sets From | yes | tests/integration/test_dashboard_browser.py::test_dragging_a_brush_on_the_series_sets_from_and_to_and_asks_again |
| the range inputs set From | yes | tests/integration/test_dashboard_browser.py::test_two_range_inputs_are_the_keyboard_route_to_the_same_zoom |
| a bin with no good values is a hatched strip | yes | tests/integration/test_dashboard_browser.py::test_a_bin_with_no_good_values_is_a_hatched_strip_labelled_in_words |
| the hatched strip is labelled in words | yes | tests/integration/test_dashboard_browser.py::test_a_bin_with_no_good_values_is_a_hatched_strip_labelled_in_words |
| Play stops after the last month | yes | node test: not ok 25 - play moves to the next month and stops after the last |
| Play is disabled under reduced motion | yes | tests/integration/test_dashboard_browser.py::test_play_is_disabled_with_a_visible_reason_when_the_system_asks_for_less_motion |
| a late SST answer is dropped | yes | tests/integration/test_dashboard_browser.py::test_a_late_answer_for_an_earlier_month_never_overwrites_the_newer_one |
| the SST cell readout gives the numbers | yes | tests/integration/test_dashboard_browser.py::test_a_cell_readout_gives_latitude_longitude_value_with_unit_and_observation_count |
| the SST map can be moved through with the arrow keys | yes | tests/integration/test_dashboard_browser.py::test_a_cell_readout_gives_latitude_longitude_value_with_unit_and_observation_count |
| a download card shows the rows limit | yes | tests/integration/test_dashboard_browser.py::test_explore_has_four_steps_and_download_cards_with_the_file_type_the_rows_and_the_limit |
| the explore status line says which page of how many | yes | tests/integration/test_dashboard_browser.py::test_the_explore_status_line_says_which_page_of_how_many |
| a skeleton stands in while a request is open | yes | tests/integration/test_dashboard_browser.py::test_a_skeleton_stands_in_while_a_request_is_open_and_is_gone_after_the_answer |
| an empty state names a next step | yes | tests/integration/test_dashboard_browser.py::test_an_empty_result_says_so_and_offers_a_next_step |
| the chosen theme is remembered | yes | tests/integration/test_dashboard_browser.py::test_the_theme_button_cycles_system_light_dark_and_the_choice_survives_a_reload |
| the page survives a throwing localStorage | yes | tests/integration/test_dashboard_browser.py::test_the_page_works_when_local_storage_throws |
| the sidebar is hidden before sign-in | yes | tests/integration/test_dashboard_browser.py::test_the_sidebar_is_not_shown_to_someone_who_is_not_signed_in |
| the narrow-screen layout (bottom bar) | yes | tests/integration/test_dashboard_browser.py::test_no_view_scrolls_sideways_at_360_px_and_the_bottom_bar_is_reachable[light] |
| the dark theme sets every colour token | yes | tests/unit/test_dashboard_contrast.py::test_the_dark_theme_overrides_every_colour_token_of_the_light_one |
| dark-theme secondary text is readable | yes | tests/unit/test_dashboard_contrast.py::test_normal_text_has_at_least_4_5_to_1[dark] |
| the dark-theme focus outline is visible | yes | tests/unit/test_dashboard_contrast.py::test_chart_marks_and_the_focus_outline_have_at_least_3_to_1[dark] |
| the light-theme focus outline is visible | yes | tests/unit/test_dashboard_contrast.py::test_chart_marks_and_the_focus_outline_have_at_least_3_to_1[light] |
| a gap strip is placed half a bin each side | yes | node test: not ok 26 - runs of empty bins become gap spans of half a bin on each side |
| a brush dragged right to left is put in order | yes | node test: not ok 24 - a brush gives the time range between two pixel positions, earliest first, kept inside the axis |
| the system theme follows the system | yes | node test: not ok 29 - the theme choice cycles system, light, dark and an unknown choice falls back to system |
| realistic data: Argo files keep is_synthetic | yes | tests/unit/test_realistic_data.py::test_every_file_says_it_is_synthetic |
| realistic data: one fixed seed | yes | tests/unit/test_realistic_data.py::test_same_seed_same_sha256_for_every_file_and_the_answer_key |
| realistic data: platform ids are fictional | yes | tests/unit/test_realistic_data.py::test_no_real_identifiers_only_fictional_ones |
| realistic data: flag 2 rate in its band | yes | tests/unit/test_realistic_data.py::test_argo_provider_flag_rates_are_in_the_bands |
| realistic data: profiles are made statically stable | yes | tests/unit/test_realistic_data.py::test_argo_profiles_are_stably_stratified_except_where_a_fault_was_injected |
| realistic data: deep water is cold | yes | tests/unit/test_realistic_data.py::test_ship_ctd_deep_water_is_cold_and_casts_go_deep |
| realistic data: the SST file stays compressed (size budget) | yes | tests/unit/test_realistic_data.py::test_size_and_runtime_budget |
| realistic data: fault labels never leak into a data file | yes | tests/unit/test_realistic_data.py::test_the_labels_of_the_faults_are_never_inside_the_data_files |
| realistic data: monsoon wind reversal | yes | tests/unit/test_realistic_data.py::test_buoys_monsoon_reversal_cyclones_outages_and_the_silent_buoy |
| realistic data: at most one cyclone per basin per year | yes | tests/unit/test_realistic_data.py::test_buoys_monsoon_reversal_cyclones_outages_and_the_silent_buoy |
| realistic data: one buoy silent for three weeks | yes | tests/unit/test_realistic_data.py::test_buoys_monsoon_reversal_cyclones_outages_and_the_silent_buoy |
| realistic data: summer upwelling off Somalia | yes | tests/unit/test_realistic_data.py::test_sst_monsoon_cycle_warm_pool_upwelling_and_cloud_gaps |
| realistic data: spring warm pool above 30 degC | yes | tests/unit/test_realistic_data.py::test_sst_monsoon_cycle_warm_pool_upwelling_and_cloud_gaps |
| realistic data: Gulf outflow tongue near 250 m | yes | tests/unit/test_realistic_data.py::test_water_masses_and_the_deep_ocean |
| realistic data: bathymetry has ridges | yes | tests/unit/test_realistic_data.py::test_bathymetry_has_shelf_slope_and_ridges |
