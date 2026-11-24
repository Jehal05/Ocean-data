"""Phase 3 mutation check: switch ONE safeguard off at a time and require that the tests notice.

For each mutation the source text is changed in place (and always restored), then the tests that are supposed to guard
the safeguard are run. A mutation that does not make one of them fail means the safeguard is not really tested.
Writes reports/phase3_mutation_check.{json,md}. Exit 0 when every mutation was caught, 1 otherwise, 2 when the
database stand-in is missing.
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

API = "tests/integration/test_review_api.py"
DBT = "tests/integration/test_review_db.py"
SVC = "components/04_expert_review_ui/review_api/service.py"
MAIN = "components/04_expert_review_ui/review_api/main.py"
SQL6 = "db/init/006_review_auth.sql"

MUTATIONS = [
    ("authorisation", MAIN, "    if not principal.at_least(need):\n", "    if False:\n", [f"{API}::test_role_matrix_every_role_against_every_endpoint"]),
    ("fail-closed on unlisted routes", MAIN, "    if need_key not in ROLE_MATRIX:\n", "    if False:\n", [f"{API}::test_a_route_without_a_policy_row_is_refused"]),
    ("comment required (API)", SVC, "    if not c:\n", "    if False:\n", [f"{API}::test_a_comment_is_required", f"{API}::test_decisions_need_a_comment"]),
    ("four-eyes", SVC, '            if s["four_eyes"]:\n', "            if False:\n", [f"{API}::test_four_eyes_on_blocks_the_ingester_and_off_allows_it"]),
    ("unreviewed flag-4 guard", SVC, "            if unreviewed and not accept_unreviewed_flag4:\n", "            if False:\n", [f"{API}::test_unreviewed_flag4_blocks_approval_unless_accepted_and_recorded"]),
    ("audit atomic with a flag change", SVC,
     '    audit(cur, principal, "flag.change" if mode == "change" else "flag.confirm", d["key"], None,\n          {"flag": new_flag, "n_cells": len(rows), "cells": changes}, comment)\n',
     '    try:\n        audit(cur, principal, "flag.change" if mode == "change" else "flag.confirm", d["key"], None,\n              {"flag": new_flag, "n_cells": len(rows), "cells": changes}, comment)\n    except Exception:\n        pass\n',
     [f"{API}::test_a_failing_audit_row_undoes_the_flag_change"]),
    ("audit atomic with a status change", SVC,
     '        audit(cur, principal, "review.start", key, {"status": "qc_done"}, {"status": "in_review"}, "review started")\n',
     '        try:\n            audit(cur, principal, "review.start", key, {"status": "qc_done"}, {"status": "in_review"}, "review started")\n        except Exception:\n            pass\n',
     [f"{API}::test_a_failing_audit_row_undoes_the_status_change"]),
    ("publish uses the approved snapshot", SVC, '" WHERE set_by_user_id IS NOT NULL", (d["id"], version))', '" WHERE set_by_user_id IS NOT NULL", (d["id"], 9223372036854775807))',
     [f"{API}::test_publish_uses_exactly_the_approved_snapshot"]),
    ("approval takes the exclusive lock", SVC, '        d = get_dataset(cur, key, lock="update")          # waits for flag changes in progress, blocks new ones\n',
     '        d = get_dataset(cur, key)\n', [f"{API}::test_an_approval_waits_for_a_change_in_progress_and_includes_it"]),
    ("cookie CSRF header", "libs/scats_auth/web.py", '    if source != "cookie" or request.method in SAFE_METHODS:\n        return True\n', "    return True\n",
     [f"{API}::test_cookie_session_needs_the_csrf_header_on_changes"]),
    ("cleared rows after a config change", "components/02_parser_api/scats_ingest/service.py", "        if f in PERSISTED_FLAGS and (o, v, l) not in wanted and o in id_to_rec:\n", "        if False:\n",
     [f"{API}::test_a_cell_that_stops_being_flagged_is_cleared_and_overrides_still_win"]),
    ("verify uploaded objects", "components/05_data_store/scats_store/writer.py", "            if got != (sha, size):\n", "            if False:\n",
     [f"{API}::test_a_corrupted_upload_is_caught_before_anything_is_published"]),
    ("human decision beats automatic (view)", SQL6, "         (set_by_user_id IS NOT NULL) DESC,          -- a person's decision beats any automatic row\n", "",
     [f"{DBT}::test_human_decision_beats_a_later_automatic_row", f"{API}::test_an_override_survives_a_qc_rerun"]),
    ("status transition guard (database)", SQL6, "CREATE TRIGGER dataset_transition_guard_trg BEFORE INSERT OR UPDATE ON dataset", "CREATE TRIGGER dataset_transition_guard_trg BEFORE INSERT ON dataset",
     [f"{DBT}::test_review_role_cannot_move_status_off_the_review_path"]),
    ("human-flag guard (database)", SQL6, "        IF NEW.comment IS NULL OR length(btrim(NEW.comment)) = 0 THEN", "        IF FALSE THEN", [f"{DBT}::test_human_flag_guard_refuses"]),
    ("login rate limit (database)", SQL6, "    IF fails_user >= p_max_user OR fails_ip >= p_max_ip THEN", "    IF FALSE THEN",
     [f"{DBT}::test_lock_after_failures_and_unlock_after_window", f"{API}::test_rate_limit_blocks_even_the_right_password_then_recovers"]),
    ("token expiry check (database)", SQL6, "s.revoked_at IS NULL AND s.expires_at > now() AND u.is_active", "s.revoked_at IS NULL AND u.is_active",
     [f"{DBT}::test_token_validity_rules", f"{API}::test_expired_and_revoked_tokens_are_refused"]),
]


def pytest(tests: list[str]) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=ROOT, capture_output=True, text=True)
    tail = [ln for ln in r.stdout.strip().splitlines() if ln.strip()][-1:] or [""]
    failed = [ln.split(" - ")[0].replace("FAILED ", "") for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
    return r.returncode, (failed[0] if failed else tail[0])


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
            bad = True
            continue
        base_rc, base_note = pytest(tests)                 # the guarding tests must pass BEFORE the change
        try:
            path.write_text(original.replace(old, new))
            rc, note = pytest(tests)
        finally:
            path.write_text(original)
        assert sha(path) == digest, f"{rel} was not restored"
        caught = base_rc == 0 and rc != 0
        bad |= not caught
        results.append({"mutation": name, "file": rel, "baseline_passes": base_rc == 0, "caught": caught,
                        "first_failing_test": note if rc != 0 else None, "guarding_tests": tests})
        print(f"{'CAUGHT ' if caught else 'MISSED '} {name:42s} -> {note if rc else 'nothing failed'}", flush=True)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "phase3_mutation_check.json").write_text(json.dumps({"mutations": results, "all_caught": not bad}, indent=1))
    lines = ["# Phase 3 mutation check", "", "One safeguard is switched off at a time; the guarding tests must then fail.", "",
             "| Safeguard switched off | Caught | First failing test |", "|---|---|---|"]
    lines += [f"| {r['mutation']} | {'yes' if r['caught'] else '**NO**'} | {r.get('first_failing_test') or r.get('note') or ''} |" for r in results]
    (out / "phase3_mutation_check.md").write_text("\n".join(lines) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
