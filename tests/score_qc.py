#!/usr/bin/env python3
"""Scoring harness: the ONLY code that reads data/test_errors/answer_key.csv.

Ingests the three error files and the five clean raw files through the real ingest service, then compares the
persisted flags in PostgreSQL (view qc_flag_current) with the answer key using the rules in
docs/phase2_design.md section 5 (as amended in section 8).

Database: SCORE_DATABASE_URL if set (must be a role that can INSERT, e.g. scats_ingest), else the local
stand-in server from tests/db_stub.py. With neither, the script exits with code 2 and says so.
Output: reports/phase2_qc_score.json and reports/phase2_qc_score.md
"""
from __future__ import annotations

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
from scats_parsers import detect  # noqa: E402
from scats_qc import load_config  # noqa: E402

KEY = ROOT / "data/test_errors/answer_key.csv"
ERR_DIR = ROOT / "data/test_errors"
RAW_DIR = ROOT / "data/raw"
DETECTED = (3, 4, 9)
BAD_FLAGS = (3, 4)


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


def fetch_flags(url: str) -> dict:
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


def main() -> int:
    url, how, stub = connect_db()
    if url is None:
        print("SKIP: no PostgreSQL available (set SCORE_DATABASE_URL or install PostgreSQL 16 binaries)")
        return 2
    cfg = load_config()
    staging = Path(tempfile.mkdtemp(prefix="scats_score_"))
    ingest_files = sorted(p for p in ERR_DIR.iterdir() if p.suffix in (".nc", ".csv") and p.name != "answer_key.csv")
    clean_files = sorted(p for p in RAW_DIR.rglob("*") if p.is_file() and p.suffix in (".nc", ".csv"))
    for f in ingest_files + clean_files:
        ingest_file(f, db_url=url, staging_dir=staging, cfg=cfg)
    flags, plat_rec = fetch_flags(url)

    key_rows = list(csv.DictReader(open(KEY)))
    per_type: dict = defaultdict(lambda: {"total": 0, "detected": 0})
    misses, expected_cells = [], defaultdict(set)
    for r in key_rows:
        path = ERR_DIR / r["file"]
        dkey = dataset_key_for(path)
        parser_cls = detect(path)
        names = common_name(parser_cls, r["variable"])
        rec = int(r["record_index"])
        if r["cast_id"]:
            rec = plat_rec[dkey].get(r["cast_id"])
        lvl = int(r["level_index"]) if r["level_index"] else -1
        et = r["error_type"]
        candidates: list[tuple] = []
        if et in ("spike", "out_of_range", "missing"):
            candidates = [(rec, names[0], lvl)]
        elif et in ("position_on_land", "position_invalid"):
            candidates = [(rec, "position", -1)]
        elif et == "timestamp_duplicate":
            candidates = [(rec, "time", -1), (rec - 1, "time", -1)]
        elif et == "timestamp_non_monotonic":
            candidates = [(rec, "time", -1)]
        elif et == "density_inversion":
            candidates = [(rec, v, l) for v in ("temperature", "salinity", "density") for l in (lvl, lvl - 1)]
            for v in ("temperature", "salinity", "density"):               # associated cells, section 8 item 2
                for l in (lvl - 1, lvl, lvl + 1):
                    expected_cells[dkey].add((rec, v, l))
        for c in candidates:
            expected_cells[dkey].add(c)
        hit = [(c, flags[dkey][c]) for c in candidates if c in flags[dkey] and flags[dkey][c][0] in DETECTED]
        per_type[et]["total"] += 1
        if hit:
            per_type[et]["detected"] += 1
        else:
            misses.append({"error_id": r["error_id"], "error_type": et, "file": r["file"], "record": rec,
                           "level": lvl, "variable": r["variable"], "candidates": [list(c) for c in candidates]})
        r["_hit"] = hit

    # false positives: flags 3/4 on cells that no key row accounts for
    fps = []
    for f in ingest_files:
        dkey = dataset_key_for(f)
        for (rec, var, lvl), (flag, test) in flags[dkey].items():
            if flag in BAD_FLAGS and (rec, var, lvl) not in expected_cells[dkey]:
                fps.append({"file": f.name, "record": rec, "variable": var, "level": lvl, "flag": flag, "test": test})
    clean_bad = {}
    for f in clean_files:
        dkey = dataset_key_for(f)
        n = sum(1 for (flag, _t) in flags[dkey].values() if flag in BAD_FLAGS)
        clean_bad[f.name] = n

    total = sum(v["total"] for v in per_type.values())
    detected = sum(v["detected"] for v in per_type.values())
    primary_ok = 0
    for r in key_rows:
        if any(t == r["expected_primary_test"] for _c, (_f, t) in r["_hit"]):
            primary_ok += 1
    fp_by_test = Counter(x["test"] for x in fps)
    result = {
        "database": how, "qc_config_version": cfg["version"], "key_rows": total, "detected": detected,
        "recall": f"{detected}/{total}", "per_type": per_type, "misses": misses,
        "persisted_test_matches_expected_primary_test": f"{primary_ok}/{total}",
        "false_positives_in_error_files": len(fps), "false_positives_by_test": dict(fp_by_test),
        "false_positive_examples": fps[:25], "flags_3_or_4_on_clean_raw_files": clean_bad,
        "clean_total": sum(clean_bad.values()),
        "note": "Persisted test_id is the test that decided the final flag (worst wins), which may differ from the key's "
                "expected_primary_test when two tests flag the same cell; detection is what is scored."}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/phase2_qc_score.json").write_text(json.dumps(result, indent=2, default=dict))
    lines = ["# Phase 2 QC score", "", f"Database: {how}", f"QC config version: {cfg['version']}", "",
             f"**Recall: {detected}/{total}**   |   false positives in error files: {len(fps)}   |   "
             f"flags 3/4 on the five clean raw files: {result['clean_total']}", "",
             "| Error type | Detected | Total |", "|---|---|---|"]
    lines += [f"| {t} | {v['detected']} | {v['total']} |" for t, v in sorted(per_type.items())]
    lines += ["", f"Persisted test equals the key's expected primary test: {result['persisted_test_matches_expected_primary_test']}",
              "", "## Misses", ""] + ([f"* {m['error_id']} {m['error_type']} {m['file']} record {m['record']} level {m['level']}" for m in misses] or ["none"])
    lines += ["", "## False positives (flag 3/4 on a cell the key does not explain)", ""]
    lines += [f"* {x['file']} record {x['record']} {x['variable']} level {x['level']} flag {x['flag']} by {x['test']}" for x in fps] or ["none"]
    lines += ["", "## Clean raw files, flags 3/4", ""] + [f"* {k}: {v}" for k, v in clean_bad.items()]
    lines += ["", "Synthetic data only. A perfect score here shows the tests work on planted errors of the kinds I chose, "
              "not that they work on real observations."]
    (ROOT / "reports/phase2_qc_score.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if stub:
        stub.stop()
    return 0 if detected == total and result["clean_total"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
