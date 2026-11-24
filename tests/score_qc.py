#!/usr/bin/env python3
"""Scoring harness: the ONLY code that reads an answer key (data/test_errors/answer_key.csv or data/blind_errors/...).

Ingests the error files and the five clean raw files through the real ingest service, then compares the persisted flags
in PostgreSQL (view qc_flag_current) with the answer key using the rules in docs/phase2_design.md section 5 (as amended in
section 8). It also re-runs the QC engine in memory to ask a second question: did the key's expected primary test fire
on the cell at all, even if another test decided the final flag?

Usage:  python tests/score_qc.py [--set original|blind]
Database: SCORE_DATABASE_URL if set (a role that can INSERT; use a FRESH database, results of earlier runs would mix in),
else the local stand-in server from tests/db_stub.py (reset before every scoring run). With neither: exit code 2.
Output: reports/phase2_qc_score.{json,md} (original) or reports/phase2_blind_score.{json,md} (blind).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import warnings
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs", "components/02_parser_api", "components/03_qc_framework", "tests"):
    sys.path.insert(0, str(ROOT / p))
warnings.filterwarnings("ignore")

import psycopg2  # noqa: E402

from scats_ingest import ingest_file  # noqa: E402
from scats_ingest.service import dataset_key_for  # noqa: E402
from scats_parsers import detect, parse_file  # noqa: E402
from scats_qc import load_config, run_qc  # noqa: E402

SETS = {
    "original": dict(dir=ROOT / "data/test_errors", key="answer_key.csv", report="phase2_qc_score",
                     title="Phase 2 QC score (original error set)"),
    "blind": dict(dir=ROOT / "data/blind_errors", key="answer_key.csv", report="phase2_blind_score",
                  title="Phase 2 QC score (BLIND error set)"),
}
RAW_DIR = ROOT / "data/raw"
DETECTED = (3, 4, 9)
BAD_FLAGS = (3, 4)
CULPRIT_TESTS = ("spike", "global_range", "regional_range")


def connect_db():
    url = os.getenv("SCORE_DATABASE_URL")
    if url:
        return url, "SCORE_DATABASE_URL", None
    import db_stub
    if db_stub.available():
        db_stub.start(fresh=True)
        return db_stub.INGEST_URL, "local stand-in PostgreSQL 16 WITHOUT PostGIS (tests/db_stub.py)", db_stub
    return None, None, None


def common_name(parser_cls, source_variable: str) -> list[str]:
    """Source column/variable name(s) in the answer key -> common names, via the parser's own YAML mapping."""
    m = parser_cls.mapping()
    out = []
    for part in source_variable.split("/"):
        part = part.strip()
        if part in m["variables"]:
            out.append(m["variables"][part]["name"])
        elif part in m.get("coordinates", {}):
            out.append(m["coordinates"][part])                     # 'time' or 'position'
        else:
            out.append(f"?{part}")
    return out


def fetch_flags(url: str):
    con = psycopg2.connect(url)
    with con, con.cursor() as cur:
        cur.execute("SELECT d.dataset_key, o.source_record, o.platform_id, q.variable_name, q.level_index, q.flag, q.test_id"
                    " FROM qc_flag_current q JOIN observation o USING (observation_id) JOIN dataset d USING (dataset_id)"
                    " WHERE q.flag IN (2,3,4,5,8,9)")
        rows = cur.fetchall()
    con.close()
    flags: dict = defaultdict(dict)
    platform_to_record: dict = defaultdict(dict)
    for key, rec, plat, var, lvl, flag, test in rows:
        flags[key][(rec, var, lvl)] = (flag, test)
        platform_to_record[key][plat] = rec
    return flags, platform_to_record


def candidates_for(et, rec, names, lvl):
    if et in ("spike", "out_of_range", "missing", "spike_small", "range_edge"):
        return [(rec, names[0], lvl)]
    if et in ("position_on_land", "position_invalid"):
        return [(rec, "position", -1)]
    if et == "timestamp_duplicate":
        return [(rec, "time", -1), (rec - 1, "time", -1)]
    if et in ("timestamp_non_monotonic", "timestamp_jump_forward"):
        return [(rec, "time", -1)]
    if et == "density_inversion":
        return [(rec, v, l) for v in ("temperature", "salinity", "density") for l in (lvl, lvl - 1)]
    raise ValueError(f"unknown error_type '{et}' in the answer key")


def score(url: str, set_name: str, tests=None, write: bool = True, stub=None) -> dict:
    spec = SETS[set_name]
    cfg = load_config()
    if stub is not None:
        stub.reset_data()
    staging = Path(tempfile.mkdtemp(prefix="scats_score_"))
    err_files = sorted(p for p in spec["dir"].iterdir() if p.suffix in (".nc", ".csv") and p.name != spec["key"])
    clean_files = sorted(p for p in RAW_DIR.rglob("*") if p.is_file() and p.suffix in (".nc", ".csv"))
    for f in err_files + clean_files:
        ingest_file(f, db_url=url, staging_dir=staging, cfg=cfg, tests=tests, reference_time="2026-11-24T00:00:00Z")
    flags, plat_rec = fetch_flags(url)

    # in-memory re-run: every finding per cell, to see which tests fired (not only the worst-wins winner)
    findings_at: dict = defaultdict(lambda: defaultdict(set))
    for f in err_files:
        ds = parse_file(f)
        for fd in run_qc(ds, cfg, tests=tests, reference_time="2026-11-24T00:00:00Z").findings:
            findings_at[dataset_key_for(f)][(fd.variable, fd.index)].add(fd.test_id)

    key_rows = list(csv.DictReader(open(spec["dir"] / spec["key"])))
    per_type = defaultdict(lambda: {"total": 0, "detected": 0})
    per_band = defaultdict(lambda: {"total": 0, "correct": 0})
    misses, false_alarms, expected_cells = [], [], defaultdict(set)
    primary_any = primary_total = 0
    for r in key_rows:
        path = spec["dir"] / r["file"]
        dkey, parser_cls = dataset_key_for(path), detect(path)
        names = common_name(parser_cls, r["variable"])
        rec = plat_rec[dkey].get(r["cast_id"]) if r["cast_id"] else int(r["record_index"])
        lvl = int(r["level_index"]) if r["level_index"] else -1
        et, expect = r["error_type"], r.get("expect", "detect") or "detect"
        cands = candidates_for(et, rec, names, lvl)
        expected_cells[dkey].update(cands)
        if et == "density_inversion":                                    # associated cells, section 8 item 2
            expected_cells[dkey].update((rec, v, l) for v in ("temperature", "salinity", "density") for l in (lvl - 1, lvl, lvl + 1))
        hit = [(c, flags[dkey][c]) for c in cands if c in flags[dkey] and flags[dkey][c][0] in DETECTED]
        band = r.get("magnitude_band") or "unbanded"
        if expect == "detect":
            per_type[et]["total"] += 1
            per_type[et]["detected"] += bool(hit)
            per_band[band]["total"] += 1
            per_band[band]["correct"] += bool(hit)
            if not hit:
                misses.append({"error_id": r["error_id"], "error_type": et, "band": band, "file": r["file"], "record": rec,
                               "level": lvl, "variable": r["variable"], "injected": r.get("injected_value", "")})
            primary_total += 1
            fired = set()
            for (rr, var, ll) in cands:
                fired |= findings_at[dkey].get((var, (rr, ll) if ll >= 0 else (rr,)), set())
            primary_any += r["expected_primary_test"] in fired
        else:                                                            # negative control: must NOT be flagged
            bad = [c for c in cands if c in flags[dkey] and flags[dkey][c][0] in BAD_FLAGS]
            per_band[band]["total"] += 1
            per_band[band]["correct"] += not bad
            if bad:
                false_alarms.append({"error_id": r["error_id"], "error_type": et, "band": band, "file": r["file"], "record": rec,
                                     "level": lvl, "variable": r["variable"], "injected": r.get("injected_value", "")})
        r["_hit"] = hit

    fps, collateral = [], []
    for f in err_files:
        dkey = dataset_key_for(f)
        culprit = {(rec, ll) for (rec, var, ll), (fl, t) in flags[dkey].items() if t in CULPRIT_TESTS and fl in BAD_FLAGS}
        for (rec, var, lvl), (flag, test) in flags[dkey].items():
            if flag in BAD_FLAGS and (rec, var, lvl) not in expected_cells[dkey]:
                fps.append({"file": f.name, "record": rec, "variable": var, "level": lvl, "flag": flag, "test": test})
            if test == "density_inversion" and flag in BAD_FLAGS and lvl >= 0 and \
               any((rec, lvl + d) in culprit for d in (-1, 0, 1)) and (flags[dkey].get((rec, var, lvl), (0, ""))[1] == "density_inversion"):
                collateral.append((f.name, rec, var, lvl))
    culprit_cells = sum(1 for f in err_files for (rec, var, ll), (fl, t) in flags[dataset_key_for(f)].items()
                        if t in CULPRIT_TESTS and fl in BAD_FLAGS)
    clean_bad = {f.name: sum(1 for (fl, _t) in flags[dataset_key_for(f)].values() if fl in BAD_FLAGS) for f in clean_files}

    total = sum(v["total"] for v in per_type.values())
    detected = sum(v["detected"] for v in per_type.values())
    result = {
        "set": set_name, "database": None, "qc_config_version": cfg["version"], "key_rows_to_detect": total,
        "detected": detected, "recall": f"{detected}/{total}", "per_type": per_type, "per_magnitude_band": per_band,
        "misses": misses, "negative_control_false_alarms": false_alarms,
        "expected_primary_test_fired_on_the_cell": f"{primary_any}/{primary_total}",
        "false_positives_in_error_files": len(fps), "false_positives_by_test": dict(Counter(x["test"] for x in fps)),
        "false_positive_examples": fps[:25],
        "collateral_density_flags": len(collateral), "culprit_cells_flagged": culprit_cells,
        "collateral_per_culprit_cell": round(len(collateral) / culprit_cells, 2) if culprit_cells else None,
        "flags_3_or_4_on_clean_raw_files": clean_bad, "clean_total": sum(clean_bad.values()),
        "note": "Detection is scored on the final persisted flag. 'expected_primary_test_fired_on_the_cell' re-runs the "
                "tests in memory and asks whether that test produced a finding on a matching cell, whichever test decided "
                "the final flag. Synthetic data written by the same author as the tests."}
    if write:
        write_report(result, spec, key_rows)
    return result


def write_report(result: dict, spec: dict, key_rows) -> None:
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / f"reports/{spec['report']}.json").write_text(json.dumps(result, indent=2, default=dict))
    L = [f"# {spec['title']}", "", f"Database: {result['database']}", f"QC config version: {result['qc_config_version']}", "",
         f"**Recall: {result['recall']}**   |   false positives in error files: {result['false_positives_in_error_files']}   |   "
         f"collateral density flags: {result['collateral_density_flags']} around {result['culprit_cells_flagged']} culprit cells   |   "
         f"flags 3/4 on the five clean raw files: {result['clean_total']}", "",
         "| Error type | Detected | Total |", "|---|---|---|"]
    L += [f"| {t} | {v['detected']} | {v['total']} |" for t, v in sorted(result["per_type"].items())]
    L += ["", "| Magnitude band | Correct | Total |", "|---|---|---|"]
    L += [f"| {b} | {v['correct']} | {v['total']} |" for b, v in sorted(result["per_magnitude_band"].items())]
    L += ["", f"Expected primary test fired on the cell (any finding, not only the winner): {result['expected_primary_test_fired_on_the_cell']}",
          "", "## Misses", ""] + ([f"* {m['error_id']} {m['error_type']} [{m['band']}] {m['file']} record {m['record']} level {m['level']} injected {m['injected']}" for m in result["misses"]] or ["none"])
    L += ["", "## Negative controls flagged (should have been left alone)", ""] + ([f"* {m['error_id']} {m['error_type']} [{m['band']}] {m['file']} record {m['record']} level {m['level']} injected {m['injected']}" for m in result["negative_control_false_alarms"]] or ["none"])
    L += ["", "## False positives (flag 3/4 on a cell the key does not explain)", ""]
    L += [f"* {x['file']} record {x['record']} {x['variable']} level {x['level']} flag {x['flag']} by {x['test']}" for x in result["false_positive_examples"]] or ["none"]
    L += ["", "## Clean raw files, flags 3/4", ""] + [f"* {k}: {v}" for k, v in result["flags_3_or_4_on_clean_raw_files"].items()]
    L += ["", "Synthetic data, written by the same author as the tests. A good score shows the code does what its author intended on "
              "planted errors of the kinds chosen, not that it works on real observations."]
    (ROOT / f"reports/{spec['report']}.md").write_text("\n".join(L) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=list(SETS), default="original")
    args = ap.parse_args()
    url, how, stub = connect_db()
    if url is None:
        print("SKIP: no PostgreSQL available (set SCORE_DATABASE_URL or install PostgreSQL 16 binaries)")
        return 2
    try:
        result = score(url, args.set, stub=stub)
        result["database"] = how
        write_report(result, SETS[args.set], [])
        print((ROOT / f"reports/{SETS[args.set]['report']}.md").read_text())
    finally:
        if stub:
            stub.stop()
    ok = result["detected"] == result["key_rows_to_detect"] and result["clean_total"] == 0
    return 0 if ok or args.set == "blind" else 1


if __name__ == "__main__":
    sys.exit(main())
