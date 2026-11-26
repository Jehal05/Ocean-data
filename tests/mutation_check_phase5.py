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
NODE = "tests/js/logic.test.mjs"
ACC = "components/07_data_access_api/access_api"

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
    ("flag shown with its label", f"{S}/views.js", "`${r[c]} ${flagInfo(r[c]).label}`", "`${r[c]}`",
     [f"{B}::test_profile_with_all_values_shows_the_flag_by_shape_and_label"]),
    ("unknown flag is never shown as good", f"{S}/logic.js", "label: `unknown flag ${Number(flag)}`", "label: \"good\"", [NODE]),
    ("map box wraps at 180 degrees", f"{S}/logic.js", "  return a <= b ? [[a, b]] : [[a, 180], [-180, b]];", "  return [[a, b]];", [NODE]),
    ("session expiry sends the user to login", f"{S}/views.js", 'dispatchEvent(new CustomEvent("scats-unauthorised"))', "void 0",
     [f"{B}::test_an_expired_session_sends_the_user_back_to_the_login_page"]),
    ("products are asked without the quality parameter", f"{S}/views.js", 'quality: type === "product" ? "" : val("quality")', 'quality: val("quality")',
     [f"{B}::test_a_stale_product_is_refused_with_the_reason_and_shown_with_a_notice_when_asked"]),
    ("only the newest request draws (map)", f"{S}/views.js", "      if (mine !== seq) return;\n      msg.notes(notices(r));\n      drawMap(", "      msg.notes(notices(r));\n      drawMap(",
     [f"{B}::test_an_older_answer_arriving_late_never_overwrites_the_newer_one"]),
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
