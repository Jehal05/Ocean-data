"""Phase 5 mutation check: switch ONE dashboard safeguard off at a time and require that a test notices.

Text of a source file is changed in place (always restored), then the tests that guard it are run: the Node logic
tests, the static test, or the browser tests (headless Chrome, real stack). A mutation no test notices means the
safeguard is not really tested. Writes reports/phase5_mutation_check.{json,md}.
Exit 0 when every mutation was caught, 1 otherwise, 2 when the database stand-in is missing.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import db_stub  # noqa: E402

S = "components/08_visualization_dashboard/dashboard/static"
B = "tests/integration/test_dashboard_browser.py"
STATIC = "tests/unit/test_dashboard_static.py"
CONTRAST = "tests/unit/test_dashboard_contrast.py"
NODE = "tests/js/logic.test.mjs"
ACC = "components/07_data_access_api/access_api"
API = "tests/integration/test_access_api.py"

# (name, file, old text (must occur once), new text, guarding tests: pytest ids, or "node")
MUTATIONS = [
    ("API strings are inserted as text, not markup", f"{S}/svg.js", "e.append(c instanceof Node ? c : document.createTextNode(String(c)))",
     "e.append(c instanceof Node ? c : (() => { const t = document.createElement('template'); t.innerHTML = String(c); return t.content; })())",
     [f"{B}::test_hostile_titles_and_platform_ids_are_text_and_run_nothing", f"{STATIC}::test_no_inner_html_outer_html_insert_adjacent_html_document_write_or_eval"]),
    ("CSRF header sent on every request", f"{S}/api.js", 'const HEADERS = { "X-Requested-With": "scats-ui" };', "const HEADERS = {};",
     [f"{B}::test_logout_needs_the_csrf_header_and_really_ends_the_session", f"{STATIC}::test_state_changing_calls_send_the_csrf_header"]),
    ("page Content-Security-Policy", f"{ACC}/main.py", "headers={\"Content-Security-Policy\": PAGE_CSP})", "headers={})",
     [f"{B}::test_csp_is_enforced_on_the_pages_and_no_view_causes_a_violation"]),
    ("truncation notice", f"{S}/logic.js", "  if (resp && resp.truncated) {\n    const total", "  if (false) {\n    const total",
     [NODE, f"{B}::test_a_truncated_page_says_so_and_offers_the_next_page"]),
    ("stale notice", f"{S}/logic.js", "  if (resp && resp.stale) out.push(", "  if (false) out.push(",
     [NODE, f"{B}::test_a_stale_product_is_refused_with_the_reason_and_shown_with_a_notice_when_asked"]),
    ("a missing value is a gap, not a zero (line)", f"{S}/logic.js", "    if (isNum(xs[i]) && isNum(ys[i])) cur.push([xs[i], ys[i]]);",
     "    if (isNum(xs[i])) cur.push([xs[i], isNum(ys[i]) ? ys[i] : 0]);",
     [NODE, f"{B}::test_series_band_line_gap_and_counts_match_the_hand_computed_hours"]),
    ("flag shown with its label", f"{S}/views.js", "`${f.code} ${f.label}`", "`${f.code}`",
     [f"{B}::test_profile_with_all_values_shows_the_flag_by_shape_and_label"]),
    ("unknown flag is never shown as good", f"{S}/logic.js", "label: `unknown flag ${Number(flag)}`", "label: \"good\"", [NODE]),
    ("footprint that crosses 180 is drawn in two pieces", f"{S}/logic.js", "  return a <= b ? [[a, b]] : [[a, 180], [-180, b]];", "  return [[a, b]];",
     [NODE, f"{B}::test_a_footprint_and_a_box_that_cross_180_degrees_are_each_drawn_in_two_pieces"]),
    ("box that crosses 180 is drawn in two pieces", f"{S}/views.js", "    const spans = box.wraps ? [[box.west, 180], [-180, box.east]] : [[box.west, box.east]];\n    for (const [a, b] of spans) svg.append",
     "    const spans = [[box.west, box.east]];\n    for (const [a, b] of spans) svg.append", [f"{B}::test_a_footprint_and_a_box_that_cross_180_degrees_are_each_drawn_in_two_pieces"]),
    ("session expiry sends the user to login", f"{S}/views.js", 'dispatchEvent(new CustomEvent("scats-unauthorised"))', "void 0",
     [f"{B}::test_an_expired_session_sends_the_user_back_to_the_login_page"]),
    ("products are asked without the quality parameter", f"{S}/views.js", 'quality: type === "product" ? "" : val("quality")', 'quality: val("quality")',
     [f"{B}::test_a_stale_product_is_refused_with_the_reason_and_shown_with_a_notice_when_asked"]),
    ("only the newest request draws (map)", f"{S}/views.js", "      if (mine !== seq) return;\n      clear(out);\n      msg.notes(notices(r));\n      drawMap(", "      clear(out);\n      msg.notes(notices(r));\n      drawMap(",
     [f"{B}::test_an_older_answer_arriving_late_never_overwrites_the_newer_one"]),

    ("/health: only a loopback caller is told about default passwords", f"{ACC}/main.py", "    if not _is_loopback(request):\n        return None\n", "",
     [f"{API}::test_only_a_loopback_caller_is_told_about_default_passwords_and_a_forwarded_header_changes_nothing"]),
    ("/health: a relayed request (forwarding header) is not the console", f"{ACC}/main.py", '    return not any(k.startswith("x-forwarded-") or k in ("forwarded", "x-real-ip") for k in (h.lower() for h in request.headers))', "    return True",
     [f"{API}::test_only_a_loopback_caller_is_told_about_default_passwords_and_a_forwarded_header_changes_nothing"]),
    ("/health: the default-password answer is cached", f"{ACC}/main.py", '    if _dp_cache["at"] is not None and now - _dp_cache["at"] < DEFAULT_PASSWORDS_CACHE_S:\n        return _dp_cache["value"]\n', "",
     [f"{API}::test_default_password_answer_is_cached_and_a_failure_is_not"]),
    ("/health: a failed lookup is not cached", f"{ACC}/main.py", "    except Exception:  # noqa: BLE001\n        return None\n    _dp_cache.update(at=now, value=value)",
     "    except Exception:  # noqa: BLE001\n        _dp_cache.update(at=now, value=None)\n        return None\n    _dp_cache.update(at=now, value=value)",
     [f"{API}::test_default_password_answer_is_cached_and_a_failure_is_not"]),
    ("login page shows no warning unless the server says true", f"{S}/app.js", "(await r.json()).default_passwords === true", "(await r.json()).default_passwords !== undefined",
     [f"{B}::test_login_page_shows_no_password_warning_when_the_server_says_null_or_false"]),
    ("an export nobody imports is dead code", f"{S}/logic.js", "function normLon(lon) {", "export function neverUsed() { return 1; }\nfunction normLon(lon) {", [f"{STATIC}::test_every_export_of_logic_and_svg_is_imported_by_another_app_file"]),
    ("SST row cap", f"{S}/views.js", "      while (resp.truncated && rows.length < ctx.maxSeriesRows) {\n        resp = await page(resp.next_page);\n        if (mine !== seq) return;",
     "      while (resp.truncated) {\n        resp = await page(resp.next_page);\n        if (mine !== seq) return;", [f"{B}::test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages"]),
    ("SST partial-draw notice", f"{S}/views.js", "      if (resp.truncated) out.append(h(\"p\", { class: \"notice\", role: \"status\" }, `Only the first ${rows.length} of ${r.n_rows_selected} cells",
     "      if (false) out.append(h(\"p\", { class: \"notice\", role: \"status\" }, `Only the first ${rows.length} of ${r.n_rows_selected} cells", [f"{B}::test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages"]),
    ("series row cap", f"{S}/views.js", "  while (resp.truncated && rows.length < ctx.maxSeriesRows) {\n    resp = await page(resp.next_page);\n    if (cancelled()) return;",
     "  while (resp.truncated) {\n    resp = await page(resp.next_page);\n    if (cancelled()) return;", [f"{B}::test_series_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages"]),
    ("series partial-draw notice", f"{S}/views.js", "  if (resp.truncated) out.append(h(\"p\", { class: \"notice\", role: \"status\" }, `Only the first ${rows.length} of ${r.n_rows_selected} rows",
     "  if (false) out.append(h(\"p\", { class: \"notice\", role: \"status\" }, `Only the first ${rows.length} of ${r.n_rows_selected} rows", [f"{B}::test_series_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages"]),
    ("band outline", f"{S}/style.css", "stroke: var(--band-edge); stroke-width: 1; }", "stroke: none; stroke-width: 1; }", [f"{CONTRAST}::test_the_check_can_fail_the_colours_it_replaced_were_below_the_limit"]),
    ("link target size in the map table ", f"{S}/style.css", "table.list td a { display: inline-block; min-width: 24px; min-height: 24px; padding: 3px 6px; }", "table.list td a { }",
     [f"{B}::test_links_in_the_map_table_are_at_least_24_px_square"]),
    ("test helper `until` fails on a real page error", B, "            if not any(m in str(e) for m in BETWEEN_PAGES):\n", "            if False:\n",
     [f"{B}::test_until_retries_a_navigation_but_nothing_else", f"{B}::test_until_fails_at_once_on_a_real_page_that_throws"]),
    ("a footprint that crosses 180 is matched by an overlapping box", f"{ACC}/service.py", "(lon_min > lon_max AND (lon_min <= %s OR lon_max >= %s))", "(lon_min > lon_max AND (lon_min <= %s AND lon_max >= %s))",
     [f"{API}::test_a_footprint_that_crosses_180_is_found_by_every_box_that_overlaps_it"]),
    ("a plain footprint is matched by a box that crosses 180", f"{ACC}/service.py", "pieces = [(b.west, b.east)] if b.west <= b.east else [(b.west, 180.0), (-180.0, b.east)]", "pieces = [(b.west, b.east)]",
     [f"{API}::test_a_plain_footprint_is_found_by_a_box_that_crosses_180_exactly_as_before"]),
    ("SST cells have an outline", f"{S}/style.css", ".cell { stroke: var(--cell-outline); stroke-width: 0.4; }", ".cell { stroke: none; }", [f"{CONTRAST}::test_every_end_of_the_sst_colour_scale_is_seen_against_the_page"]),
    ("SST colour bar has a frame", f"{S}/views.js", 'svg.append(s("rect", { class: "plot", x: 64, y: 6, width: 600, height: 26, fill: "none" }));', "void 0;",
     [f"{B}::test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages"]),

    # ---- Chart room (UI redesign) ----
    ("a flag chip carries the marker shape, not only words", f"{S}/views.js", 'marker(f.shape, 6, 6, `flag${f.code ?? "none"}`)), text);', 's("g")), text);',
     [f"{B}::test_flag_chips_carry_the_marker_shape_and_the_words_in_the_legend_the_table_and_the_strip"]),
    ("table flag cells are chips", f"{S}/views.js", "? chip(r[c]) : isNum(r[c])", "? String(r[c]) : isNum(r[c])",
     [f"{B}::test_flag_chips_carry_the_marker_shape_and_the_words_in_the_legend_the_table_and_the_strip"]),
    ("the legend names all six flags", f"{S}/views.js", "[0, 1, 2, 3, 4, 9].map(chip)", "[1, 2, 3, 4, 9].map(chip)",
     [f"{B}::test_flag_chips_carry_the_marker_shape_and_the_words_in_the_legend_the_table_and_the_strip"]),
    ("the provenance strip shows the whole checksum in its title", f"{S}/views.js", "title: c.qc_config_sha256 }, shortSha(info.qc_config_sha256))", 'title: "" }, shortSha(info.qc_config_sha256))',
     [f"{B}::test_every_figure_has_a_provenance_strip_with_versions_a_short_checksum_and_flag_chips"]),
    ("a dataset's flags used are shown as chips", f"{S}/views.js", "const info = Array.isArray(info0.flags_used) || quality === null ? info0 : { ...info0, flags_used: flagsFor(quality) };", "const info = info0;",
     [f"{B}::test_every_figure_has_a_provenance_strip_with_versions_a_short_checksum_and_flag_chips"]),
    ("'Copied' is announced as a status", f"{S}/views.js", 'const note = h("span", { class: "prov-copied", role: "status" });', 'const note = h("span", { class: "prov-copied" });',
     [f"{B}::test_the_copy_buttons_copy_the_checksum_and_a_one_line_caption_and_say_so"]),
    ("a refused clipboard falls back to a selected text field", f"{S}/views.js", "  box.focus();\n  box.select();\n", "  box.focus();\n",
     [f"{B}::test_when_the_clipboard_is_refused_the_text_is_shown_selected_in_a_visible_field"]),
    ("the caption says when the data is synthetic", f"{S}/logic.js", "return synthetic ? `${line} The data is synthetic (made for testing, not real ocean data).` : line;", "return line;", [NODE, f"{B}::test_the_copy_buttons_copy_the_checksum_and_a_one_line_caption_and_say_so"]),
    ("Escape closes the map card", f"{S}/views.js", 'if (e.key === "Escape" && !card.hidden)', 'if (e.key === "Never" && !card.hidden)',
     [f"{B}::test_a_footprint_card_opens_on_hover_and_on_focus_enter_pins_it_and_escape_closes_it"]),
    ("the map has a degree graticule every 30 degrees", f"{S}/views.js", "for (let v = Math.ceil(xa.min / 30) * 30; v <= xa.max; v += 30)", "for (let v = Math.ceil(xa.min / 30) * 30; v <= xa.max; v += 25)",
     [f"{B}::test_the_map_has_an_ocean_a_degree_graticule_and_a_legend_by_type"]),
    ("a grid footprint is dotted (a pattern, not only a colour)", f"{S}/style.css", ".fp.type-grid .fprect { fill: url(#fpdots); }", ".fp.type-grid .fprect { }",
     [f"{B}::test_the_map_has_an_ocean_a_degree_graticule_and_a_legend_by_type"]),
    ("the profile crosshair highlights the table row", f"{S}/views.js", 'table.querySelector(`tr[data-row="${i}"]`)?.classList.add("hl");', 'table.querySelector(`tr[data-row="${i}"]`)?.classList.add("x");',
     [f"{B}::test_the_profile_crosshair_readout_and_table_row_follow_the_pointer_and_the_arrow_keys"]),
    ("the profile readout carries the flag chip", f"{S}/views.js", "f === null || f === undefined ? null : chip(f)", "null",
     [f"{B}::test_the_profile_crosshair_readout_and_table_row_follow_the_pointer_and_the_arrow_keys"]),
    ("the series brush sets From", f"{S}/views.js", 'document.getElementById("time_from").value = isoDay(a);', "void 0;",
     [f"{B}::test_dragging_a_brush_on_the_series_sets_from_and_to_and_asks_again"]),
    ("the range inputs set From", f"{S}/views.js", 'document.getElementById("time_from").value = tFrom.textContent;', "void 0;",
     [f"{B}::test_two_range_inputs_are_the_keyboard_route_to_the_same_zoom"]),
    ("a bin with no good values is a hatched strip", f"{S}/views.js", 'svg.append(s("rect", { class: "gap",', 'svg.append(s("rect", { class: "nogap",',
     [f"{B}::test_a_bin_with_no_good_values_is_a_hatched_strip_labelled_in_words"]),
    ("the hatched strip is labelled in words", f"{S}/views.js", 's("title", {}, "no good values")', 's("title", {}, "")',
     [f"{B}::test_a_bin_with_no_good_values_is_a_hatched_strip_labelled_in_words"]),
    ("Play stops after the last month", f"{S}/logic.js", "export const nextIndex = (i, n) => (i + 1 < n ? i + 1 : null);", "export const nextIndex = (i, n) => (i + 1 < n ? i + 1 : 0);",
     [NODE, f"{B}::test_play_steps_one_month_at_a_time_and_stops_at_the_last_month"]),
    ("Play is disabled under reduced motion", f"{S}/views.js", "disabled: reduce || monthList.length < 2", "disabled: monthList.length < 2",
     [f"{B}::test_play_is_disabled_with_a_visible_reason_when_the_system_asks_for_less_motion"]),
    ("a late SST answer is dropped", f"{S}/views.js",
     "      if (mine !== seq) return;\n      msg.notes(notices(r));\n      let rows = r.rows, resp = r;\n      while (resp.truncated && rows.length < ctx.maxSeriesRows) {\n        resp = await page(resp.next_page);\n        if (mine !== seq) return;\n        rows = rows.concat(resp.rows);\n      }\n      clear(out);",
     "      msg.notes(notices(r));\n      let rows = r.rows, resp = r;\n      while (resp.truncated && rows.length < ctx.maxSeriesRows) {\n        resp = await page(resp.next_page);\n        rows = rows.concat(resp.rows);\n      }\n      clear(out);",
     [f"{B}::test_a_late_answer_for_an_earlier_month_never_overwrites_the_newer_one"]),
    ("the SST cell readout gives the numbers", f"{S}/views.js", 'readout.textContent = cellReadout(rows[i], v, r.units[v] || "");', 'readout.textContent = "";',
     [f"{B}::test_a_cell_readout_gives_latitude_longitude_value_with_unit_and_observation_count"]),
    ("the SST map can be moved through with the arrow keys", f"{S}/views.js", "if (next >= 0) pick(next);", ";",
     [f"{B}::test_a_cell_readout_gives_latitude_longitude_value_with_unit_and_observation_count"]),
    ("a download card shows the rows limit", f"{S}/views.js", "h(\"p\", { class: \"dllimit\" }, `Rows limit for a download: ${lim.max_rows_download}`)", 'h("p", { class: "dllimit" }, "")',
     [f"{B}::test_explore_has_four_steps_and_download_cards_with_the_file_type_the_rows_and_the_limit"]),
    ("the explore status line says which page of how many", f"{S}/views.js", "`Page ${r.page} of ${pages} (${r.n_rows_selected} rows selected)`", "`Page ${r.page} of 1 (${r.n_rows_selected} rows selected)`",
     [f"{B}::test_the_explore_status_line_says_which_page_of_how_many"]),
    ("a skeleton stands in while a request is open", f"{S}/views.js", "const busy = (out) => { clear(out); out.append(skeleton()); };", "const busy = (out) => { clear(out); };",
     [f"{B}::test_a_skeleton_stands_in_while_a_request_is_open_and_is_gone_after_the_answer"]),
    ("an empty state names a next step", f"{S}/views.js", 'h("p", { class: "hint" }, step)', "null",
     [f"{B}::test_an_empty_result_says_so_and_offers_a_next_step"]),
    ("the chosen theme is remembered", f"{S}/app.js", "try { localStorage.setItem(THEME_KEY, themeChoice); }", "try { void 0; }",
     [f"{B}::test_the_theme_button_cycles_system_light_dark_and_the_choice_survives_a_reload"]),
    ("the page survives a throwing localStorage", f"{S}/app.js", 'try { const v = localStorage.getItem(THEME_KEY); if (v === "light" || v === "dark") themeChoice = v; } catch { /* storage may be blocked; the page still works */ }',
     'const v = localStorage.getItem(THEME_KEY); if (v === "light" || v === "dark") themeChoice = v;',
     [f"{B}::test_the_page_works_when_local_storage_throws"]),
    ("the sidebar is hidden before sign-in", f"{S}/app.js", 'document.body.classList.toggle("anon", !ME);', 'document.body.classList.toggle("anon", false);',
     [f"{B}::test_the_sidebar_is_not_shown_to_someone_who_is_not_signed_in"]),
    ("the narrow-screen layout (bottom bar)", f"{S}/style.css", "@media (max-width: 760px) {", "@media (max-width: 1px) {",
     [f"{B}::test_no_view_scrolls_sideways_at_360_px_and_the_bottom_bar_is_reachable"]),
    ("the dark theme sets every colour token", f"{S}/style.css", "--bg: #0e1923; --surface: #15232f;", "--bg: #0e1923;",
     [f"{CONTRAST}::test_the_dark_theme_overrides_every_colour_token_of_the_light_one"]),
    ("dark-theme secondary text is readable", f"{S}/style.css", "--muted: #a8b6c1;", "--muted: #44525d;",
     [f"{CONTRAST}::test_normal_text_has_at_least_4_5_to_1"]),
    ("the dark-theme focus outline is visible", f"{S}/style.css", "--focus: #ffb454;", "--focus: #2a1a00;",
     [f"{CONTRAST}::test_chart_marks_and_the_focus_outline_have_at_least_3_to_1"]),
    ("the light-theme focus outline is visible", f"{S}/style.css", "--focus: #b35c00;", "--focus: #e08a00;",
     [f"{CONTRAST}::test_chart_marks_and_the_focus_outline_have_at_least_3_to_1"]),
    ("a gap strip is placed half a bin each side", f"{S}/logic.js", "out.push([times[from] - step / 2, times[i - 1] + step / 2]);", "out.push([times[from], times[i - 1]]);", [NODE]),
    ("a brush dragged right to left is put in order", f"{S}/logic.js", "return a <= b ? [a, b] : [b, a];", "return [a, b];", [NODE]),
    ("the system theme follows the system", f"{S}/logic.js", "(choice === \"dark\" ? true : choice === \"light\" ? false : Boolean(systemDark))", "(choice === \"dark\" ? true : choice === \"light\" ? false : false)", [NODE]),
]


def run(tests: list[str]) -> tuple[int, str]:
    py = [t for t in tests if t != NODE]
    rc, note = 0, ""
    if NODE in tests:
        r = subprocess.run(["node", "--test", NODE], cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            return r.returncode, "node test: " + next((ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("not ok")), "failed")
    if py:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *py], cwd=ROOT, capture_output=True, text=True)
        failed = [ln.split(" - ")[0].replace("FAILED ", "") for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
        rc, note = r.returncode, (failed[0] if failed else ([ln for ln in r.stdout.splitlines() if ln.strip()][-1:] or [""])[0])
    return rc, note


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    if not db_stub.available():
        print("SKIP: no local PostgreSQL 16 binaries")
        return 2
    results, bad = [], False
    for name, rel, old, new, tests in MUTATIONS:
        path = ROOT / rel
        original, digest = path.read_text(), sha(path)
        count = original.count(old)
        if count != 1:
            results.append({"mutation": name, "file": rel, "caught": False, "note": f"the text to change occurs {count} times (must be 1)"})
            print(f"MISSED  {name}: text occurs {count} times")
            bad = True
            continue
        base_rc, _ = run(tests)
        try:
            path.write_text(original.replace(old, new))
            rc, note = run(tests)
        finally:
            path.write_text(original)
        assert sha(path) == digest, f"{rel} was not restored"
        caught = base_rc == 0 and rc != 0
        bad |= not caught
        results.append({"mutation": name, "file": rel, "baseline_passes": base_rc == 0, "caught": caught, "first_failing_test": note if rc else None, "guarding_tests": tests})
        print(f"{'CAUGHT ' if caught else 'MISSED '} {name:50s} -> {note if rc else 'nothing failed'}", flush=True)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "phase5_mutation_check.json").write_text(json.dumps({"mutations": results, "all_caught": not bad}, indent=1))
    lines = ["# Phase 5 mutation check", "", "One dashboard safeguard is switched off at a time; the guarding tests must then fail.", "",
             "| Safeguard switched off | Caught | First failing test |", "|---|---|---|"]
    lines += [f"| {r['mutation']} | {'yes' if r['caught'] else '**NO**'} | {r.get('first_failing_test') or r.get('note') or ''} |" for r in results]
    (out / "phase5_mutation_check.md").write_text("\n".join(lines) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
