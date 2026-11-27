#!/usr/bin/env python3
"""Run the QC framework over the REALISTIC synthetic set (data/realistic) and score it against its ground truth
(data/blind_errors/realistic/answer_key.csv, written by the generator). Reuses the matching rules of score_qc.py.

    python3 tests/score_realistic.py [--json reports/realistic_score.json]

Reports: counts per dataset, flags by value (per file group), recall per injected fault type, faults the QC does not cover
(expect=not_covered, reported honestly, not counted as misses), natural false positives (flag 3/4 outside any injected fault)
and ingest timings. Needs the local PostgreSQL stand-in (tests/db_stub.py) or SCORE_DATABASE_URL."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import score_qc as S
from scats_ingest import ingest_file
from scats_ingest.service import dataset_key_for
from scats_parsers import detect
from scats_qc import load_config

ROOT = S.ROOT
DATA = ROOT / "data/realistic"
KEY = ROOT / "data/blind_errors/realistic/answer_key.csv"


def score(url: str, stub=None) -> dict:
    cfg = load_config()
    if stub is not None:
        stub.reset_data()
    staging = Path(tempfile.mkdtemp(prefix="scats_real_"))
    files = sorted(p for p in DATA.rglob("*") if p.suffix in (".nc", ".csv"))
    timings = {}
    for f in files:
        t0 = time.time()
        ingest_file(f, db_url=url, staging_dir=staging, cfg=cfg, reference_time="2026-11-24T00:00:00Z")
        timings[f.name] = round(time.time() - t0, 2)
    flags, plat_rec = S.fetch_flags(url)
    import psycopg2
    con = psycopg2.connect(url)
    with con, con.cursor() as cur:
        cur.execute("SELECT d.dataset_key, q.flag, count(*) FROM qc_flag_current q JOIN observation o USING (observation_id) JOIN dataset d USING (dataset_id) GROUP BY 1, 2")
        by_flag: dict = defaultdict(dict)
        for k, fl, n in cur.fetchall():
            by_flag[k][int(fl)] = n
        cur.execute("SELECT d.dataset_key, count(*) FROM observation o JOIN dataset d USING (dataset_id) GROUP BY 1")
        obs = dict(cur.fetchall())
    con.close()
    rows = list(csv.DictReader(open(KEY)))
    per_type = defaultdict(lambda: {"total": 0, "detected": 0})
    not_covered = defaultdict(int)
    misses, expected_cells = [], defaultdict(set)
    for r in rows:
        path = DATA / r["file"] if (DATA / r["file"]).exists() else next(DATA.rglob(r["file"]))
        dkey, pc = dataset_key_for(path), detect(path)
        names = S.common_name(pc, r["variable"])
        rec = plat_rec[dkey].get(r["cast_id"]) if r["cast_id"] else int(r["record_index"])
        lvl = int(r["level_index"]) if r["level_index"] else -1
        et = r["error_type"]
        if r["expect"] == "not_covered":
            not_covered[et] += 1
            continue
        cands = S.candidates_for(et, rec, names, lvl)
        expected_cells[dkey].update(cands)
        if et == "density_inversion":
            expected_cells[dkey].update((rec, v, l) for v in ("temperature", "salinity", "density") for l in (lvl - 1, lvl, lvl + 1))
        hit = [c for c in cands if c in flags[dkey] and flags[dkey][c][0] in S.DETECTED]
        per_type[et]["total"] += 1
        per_type[et]["detected"] += bool(hit)
        if not hit:
            misses.append({"error_id": r["error_id"], "type": et, "file": r["file"], "record": rec, "level": lvl, "variable": r["variable"]})
    fps = defaultdict(int)
    for dkey, cells in flags.items():
        for (rec, var, lvl), (flag, test) in cells.items():
            if flag in S.BAD_FLAGS and (rec, var, lvl) not in expected_cells[dkey]:
                fps[test] += 1
    total = sum(v["total"] for v in per_type.values())
    det = sum(v["detected"] for v in per_type.values())
    return {"observations": obs, "flags_by_value": {k: dict(sorted(v.items())) for k, v in by_flag.items()}, "per_type": dict(per_type),
            "recall": f"{det}/{total}", "misses": misses, "not_covered_by_qc": dict(not_covered),
            "unexpected_flag_3_4_by_test": dict(fps), "ingest_seconds": timings}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=ROOT / "reports/realistic_score.json")
    a = ap.parse_args()
    url, how, stub = S.connect_db()
    if url is None:
        print("SKIP: no PostgreSQL available")
        return 2
    try:
        res = score(url, stub)
    finally:
        if stub:
            stub.stop()
    a.json.parent.mkdir(exist_ok=True)
    a.json.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
