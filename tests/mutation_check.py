#!/usr/bin/env python3
"""Mutation check: disable each of the ten QC tests in turn and re-score the ORIGINAL error set.

A test that is meant to catch planted errors must make the score worse when it is removed. Two numbers are watched:
  recall            detected key rows (final persisted flag 3, 4 or 9)
  primary fired     key rows whose expected primary test produced a finding on the cell
Recall alone is a weak signal because several tests flag the same cell (e.g. regional_range also flags a value that
global_range flags), so removal of one often changes nothing; 'primary fired' drops whenever the named test is removed.
Tests that are never the expected primary test of any key row (format_consistency, regional_range, timestamp_valid) cannot be
checked this way: they are listed as 'not exercised by the answer key' and rely on their own unit tests.
Writes reports/phase2_mutation_check.json and .md. Exit 0 if every exercised test shows a drop in 'primary fired'.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import score_qc as S  # noqa: E402  (sets sys.path for the rest)
from scats_qc import ALL_TESTS  # noqa: E402
from scats_qc.base import TestResult  # noqa: E402


def disabled(cls):
    inst = cls()
    inst.run = lambda ds, cfg: TestResult()
    return inst


def main() -> int:
    url, how, stub = S.connect_db()
    if url is None:
        print("SKIP: no PostgreSQL available")
        return 2
    try:
        base = S.score(url, "original", write=False, stub=stub)
        rows, exit_code = [], 0
        primary_tests = {r["expected_primary_test"] for r in __import__("csv").DictReader(open(S.SETS["original"]["dir"] / "answer_key.csv"))}
        for cls in ALL_TESTS:
            tests = [disabled(c) if c is cls else c() for c in ALL_TESTS]
            res = S.score(url, "original", tests=tests, write=False, stub=stub)
            fired = lambda r: int(r["expected_primary_test_fired_on_the_cell"].split("/")[0])  # noqa: E731
            exercised = cls.test_id in primary_tests
            row = {"test_id": cls.test_id, "recall_without_it": res["recall"], "primary_fired_without_it": res["expected_primary_test_fired_on_the_cell"],
                   "exercised_by_answer_key": exercised, "recall_dropped": res["detected"] < base["detected"],
                   "primary_fired_dropped": fired(res) < fired(base)}
            if exercised and not row["primary_fired_dropped"]:
                exit_code = 1
            rows.append(row)
    finally:
        if stub:
            stub.stop()
    out = {"baseline_recall": base["recall"], "baseline_primary_fired": base["expected_primary_test_fired_on_the_cell"], "tests": rows,
           "removal_changes_nothing": [r["test_id"] for r in rows if not r["recall_dropped"] and not r["primary_fired_dropped"]],
           "recall_unchanged_when_removed": [r["test_id"] for r in rows if not r["recall_dropped"]]}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/phase2_mutation_check.json").write_text(json.dumps(out, indent=2))
    L = ["# Phase 2 mutation check (original error set)", "", f"Baseline: recall {base['recall']}, primary test fired {base['expected_primary_test_fired_on_the_cell']}", "",
         "| Test removed | Recall without it | Primary fired without it | In answer key? | Verdict |", "|---|---|---|---|---|"]
    for r in rows:
        verdict = ("score drops" if r["primary_fired_dropped"] or r["recall_dropped"] else
                   "NO CHANGE: needs a better test" if r["exercised_by_answer_key"] else "not exercised by the answer key (unit tests only)")
        L.append(f"| {r['test_id']} | {r['recall_without_it']} | {r['primary_fired_without_it']} | {'yes' if r['exercised_by_answer_key'] else 'no'} | {verdict} |")
    L += ["", f"Recall unchanged when removed (another test flags the same cells): {', '.join(out['recall_unchanged_when_removed']) or 'none'}"]
    (ROOT / "reports/phase2_mutation_check.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
