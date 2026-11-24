"""Recompute what PostgreSQL should hold from the staging file and report every difference."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg2

from . import rules
from .staging import read_staging


@dataclass
class ConsistencyReport:
    dataset_key: str
    ok: bool
    expected_rows: int
    db_rows: int
    missing_in_db: list = field(default_factory=list)       # expected but absent
    extra_in_db: list = field(default_factory=list)         # present but not expected
    overall_flag_mismatch: list = field(default_factory=list)
    observations_expected: int = 0
    observations_db: int = 0


def verify_flag_consistency(dataset_key: str, *, db_url: str, staging_dir: Path | str) -> ConsistencyReport:
    ds = read_staging(staging_dir, dataset_key)
    want = {r.key: r.flag for r in rules.expected_rows(ds)}
    overall = rules.overall_flags(ds)
    con = psycopg2.connect(db_url)
    try:
        with con.cursor() as cur:
            cur.execute("SELECT dataset_id FROM dataset WHERE dataset_key = %s", (dataset_key,))
            row = cur.fetchone()
            if row is None:
                return ConsistencyReport(dataset_key, False, len(want), 0, missing_in_db=["dataset not in database"])
            cur.execute("SELECT o.source_record, q.variable_name, q.level_index, q.flag FROM qc_flag_current q"
                        " JOIN observation o USING (observation_id) WHERE o.dataset_id = %s"
                        " AND q.set_by_user_id IS NULL AND q.flag IN (2,3,4,5,8,9)", (row[0],))
            have = {(a, b, c): d for a, b, c, d in cur.fetchall()}
            cur.execute("SELECT source_record, overall_flag FROM observation WHERE dataset_id = %s", (row[0],))
            db_overall = dict(cur.fetchall())
    finally:
        con.close()
    missing = sorted(k for k in want if have.get(k) != want[k])
    extra = sorted(k for k in have if k not in want)
    bad_overall = [i for i in range(len(overall)) if db_overall.get(i) != int(overall[i])]
    return ConsistencyReport(dataset_key, not (missing or extra or bad_overall or len(db_overall) != len(overall)),
                             len(want), len(have), missing[:20], extra[:20], bad_overall[:20], len(overall), len(db_overall))
