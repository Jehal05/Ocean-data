"""Phase 4 mutation check: switch ONE safeguard off at a time and require that the tests notice.

For each mutation the source text is changed in place (and always restored), then the tests that are supposed to guard
the safeguard are run. A mutation that does not make one of them fail means the safeguard is not really tested.
Writes reports/phase4_mutation_check.{json,md}. Exit 0 when every mutation was caught, 1 otherwise, 2 when the
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

API = "tests/integration/test_access_api.py"
PIPE = "tests/integration/test_products_pipeline.py"
DBT = "tests/integration/test_products_db.py"
UNI = "tests/unit/test_access.py"
UPR = "tests/unit/test_products.py"
ACC = "components/07_data_access_api/access_api"
PRD = "components/06_products_pipeline/scats_products"
SQL7 = "db/init/007_products_access.sql"

MUTATIONS = [
    ("authorisation check", f"{ACC}/main.py", "    if not principal.at_least(need):\n", "    if False:\n", [f"{API}::test_role_matrix_every_role_against_every_endpoint"]),
    ("fail-closed on an unlisted route", f"{ACC}/main.py", "    if need_key not in ROLE_MATRIX:\n", "    if False:\n", [f"{API}::test_a_route_without_a_policy_row_is_refused"]),
    ("published-only input filter", f"{PRD}/pipeline.py", '    if d["status"] != "published" or d["store_id"] is None:\n', "    if False:\n", [f"{PIPE}::test_only_published_data_is_ever_read"]),
    ("good-flag filter", f"{PRD}/binning.py", "np.isfinite(values) & np.isin(qc, list(good_flags))", "np.isfinite(values)",
     [f"{UPR}::test_flags_3_4_9_never_enter_a_product", f"{PIPE}::test_the_product_values_in_the_store_are_the_hand_computed_ones"]),
    ("stale marking (trigger)", SQL7, "CREATE TRIGGER dataset_stale_trg AFTER UPDATE OF status ON dataset", "CREATE TRIGGER dataset_stale_trg AFTER UPDATE OF title ON dataset",
     [f"{DBT}::test_the_trigger_marks_products_stale_when_the_dataset_leaves_published"]),
    ("stale marking (view notices a new review)", SQL7, "        OR s.review_version  IS DISTINCT FROM p.source_review_version) AS stale,", "        ) AS stale,",
     [f"{DBT}::test_the_view_notices_a_new_review_version"]),
    ("query-log atomicity", f"{ACC}/service.py",
     "            write_log(cur, principal, action=action, target_type=typ, target_key=key, version=col.version, data_version=col.content_sha256,\n                      qc_sha=col.qc_config_sha256, flt=flt, fmt=fmt, n_rows=n_rows, n_bytes=n_bytes)\n",
     "            pass\n", [f"{API}::test_if_the_log_row_cannot_be_written_nothing_is_returned", f"{API}::test_every_answered_request_is_logged_and_refused_ones_are_not"]),
    ("row limit", f"{ACC}/service.py", "        if plan.n_rows > cap:\n", "        if False:\n", [f"{API}::test_every_limit_is_enforced_once"]),
    ("CSV formula neutralisation", f"{ACC}/outputs.py", "    return \"'\" + s if s.startswith(FORMULA_START) else s\n", "    return s\n",
     [f"{UNI}::test_csv_cells_that_look_like_formulas_are_neutralised_but_numbers_are_not", f"{API}::test_csv_cells_that_would_run_as_formulas_are_neutralised"]),
    ("hostile-filter validation", f"{ACC}/filters.py", "    if not NUM_RE.match(text):\n", "    if False:\n", [f"{UNI}::test_hostile_or_malformed_filters_are_refused"]),
    ("lazy read (reads everything)", "components/05_data_store/scats_store/lazy.py", "        data = await asyncio.to_thread(self._fetch, key)\n",
     "        for _k in list(self._objects):\n            await asyncio.to_thread(self._fetch, _k)\n        data = await asyncio.to_thread(self._fetch, key)\n",
     [f"{UNI}::test_a_small_window_reads_only_the_chunks_it_needs", f"{API}::test_a_small_request_reads_few_objects"]),
    ("determinism (a timestamp in the product)", f"{PRD}/pipeline.py", '"flags_used": list(cfg.good_flags), "interpolation": "none",',
     '"flags_used": list(cfg.good_flags), "interpolation": "none", "built_now": str(__import__("time").time_ns()),', [f"{PIPE}::test_the_same_inputs_and_code_give_the_same_checksums"]),
    ("object checksum check (lazy store)", "components/05_data_store/scats_store/lazy.py", "        if len(data) != info[\"size\"] or hashlib.sha256(data).hexdigest() != info[\"sha256\"]:\n", "        if False:\n",
     [f"{UNI}::test_a_changed_object_or_manifest_is_refused", f"{API}::test_a_changed_object_in_the_store_is_caught_and_nothing_is_returned"]),
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
    (out / "phase4_mutation_check.json").write_text(json.dumps({"mutations": results, "all_caught": not bad}, indent=1))
    lines = ["# Phase 4 mutation check", "", "One safeguard is switched off at a time; the guarding tests must then fail.", "",
             "| Safeguard switched off | Caught | First failing test |", "|---|---|---|"]
    lines += [f"| {r['mutation']} | {'yes' if r['caught'] else '**NO**'} | {r.get('first_failing_test') or r.get('note') or ''} |" for r in results]
    (out / "phase4_mutation_check.md").write_text("\n".join(lines) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
