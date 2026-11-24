"""Parse -> QC -> stage -> write to PostgreSQL. Safe to run twice on the same file."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras as pgx

from scats_common import MODEL
from scats_parsers import ParseError, parse_file
from scats_qc import load_config, run_qc

from . import rules
from .staging import slug, write_staging

ACTOR = "scats_ingest"


class IngestError(RuntimeError):
    pass


class IngestConflict(IngestError):
    """Same dataset key, different file content."""


@dataclass
class IngestResult:
    dataset_key: str
    dataset_id: int
    dataset_type: str
    n_observations: int
    flag_rows_expected: int
    flag_rows_inserted: int
    flag_counts: dict
    test_counts: dict
    overall_flag: int
    qc_config_version: str
    staging_path: str
    reingest: bool
    status: str = "qc_done"


def jsonable(x):
    """Make anything JSON-safe for PostgreSQL jsonb (NaN is not valid there)."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, (np.ndarray,)):
        return jsonable(x.tolist())
    return x


def _dumps(x) -> str:
    return json.dumps(jsonable(x), allow_nan=False)


def _ts(value):
    if value in (None, "", "not applicable"):
        return None
    return pd.Timestamp(value).tz_localize("UTC") if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value)


def _bbox_wkt(a: dict):
    try:
        la0, la1, lo0, lo1 = (float(a[k]) for k in ("geospatial_lat_min", "geospatial_lat_max",
                                                    "geospatial_lon_min", "geospatial_lon_max"))
    except (KeyError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (la0, la1, lo0, lo1)) or la0 >= la1 or lo0 >= lo1:
        return None
    return f"POLYGON(({lo0} {la0}, {lo1} {la0}, {lo1} {la1}, {lo0} {la1}, {lo0} {la0}))"


def dataset_key_for(path: Path) -> str:
    return slug(Path(path).stem)


def _observation_tuples(ds, dataset_id: int, source_file: str, overall: np.ndarray) -> list[tuple]:
    kind, n = rules.kind_of(ds), rules.n_observations(ds)
    out = []
    for i in range(n):
        platform = obs_time = wkt = dmin = dmax = n_lev = None
        if kind in ("profile", "timeseries"):
            platform = str(ds["platform_id"].values[i])
            t = ds["time"].values[i]
            obs_time = None if np.isnat(t) else str(_ts(t))
            lat, lon = float(ds["latitude"].values[i]), float(ds["longitude"].values[i])
            if math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180:
                wkt = f"POINT({lon} {lat})"
            if kind == "profile":
                d = ds["depth"].values[i]
                d = d[np.isfinite(d)]
                n_lev = int(ds["n_levels"].values[i])
                if d.size:
                    dmin, dmax = float(d.min()), float(d.max())
        elif kind == "grid":
            t = ds["time"].values[i]
            obs_time = None if np.isnat(t) else str(_ts(t))
        out.append((dataset_id, platform, obs_time, wkt, dmin, dmax, n_lev, source_file, i, int(overall[i])))
    return out


def _audit(cur, action: str, key: str, before, after, comment: str):
    cur.execute(
        "INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, before_state, after_state, comment)"
        " VALUES (NULL, %s, %s, 'dataset', %s, %s::jsonb, %s::jsonb, %s)",
        (ACTOR, action, key, None if before is None else _dumps(before), _dumps(after), comment))


def ingest_file(path: Path | str, *, db_url: str, staging_dir: Path | str, cfg: dict | None = None) -> IngestResult:
    path = Path(path)
    cfg = cfg or load_config()
    ds = parse_file(path)                                     # ParseError propagates to the caller
    report = run_qc(ds, cfg)
    key = dataset_key_for(path)
    overall = rules.overall_flags(ds)
    rows = rules.expected_rows(ds)
    staged = write_staging(ds, staging_dir, key)

    con = psycopg2.connect(db_url)
    try:
        with con:                                             # one transaction: all or nothing
            with con.cursor() as cur:
                return _write(cur, ds, report, cfg, path, key, staged, overall, rows)
    finally:
        con.close()


def _write(cur, ds, report, cfg, path, key, staged, overall, rows) -> IngestResult:
    a = ds.attrs
    sha = a["original_checksum_sha256"]
    cur.execute("SELECT dataset_id, checksum_sha256 FROM dataset WHERE dataset_key = %s", (key,))
    existing = cur.fetchone()
    if existing and existing[1] != sha:
        raise IngestConflict(f"dataset '{key}' already exists with a different file checksum; "
                             f"rename the file or remove the old dataset first")
    values = (key, a["title"], a["dataset_type"], a["source"], a.get("instrument"), a["is_synthetic"] == "true",
              a["license"], _ts(a["time_coverage_start"]), _ts(a["time_coverage_end"]), _bbox_wkt(a), "raw",
              f"file://{path.resolve()}", Path(path).suffix.lstrip(".").lower(), sha, a["Conventions"],
              _dumps({k: v for k, v in a.items()}))
    if existing:
        dataset_id = existing[0]
        cur.execute("UPDATE dataset SET status = 'qc_running', updated_at = now() WHERE dataset_id = %s", (dataset_id,))
    else:
        cur.execute(
            "INSERT INTO dataset (dataset_key, title, dataset_type, source, instrument, is_synthetic, license,"
            " time_start, time_end, bbox, zone, storage_uri, original_format, checksum_sha256, conventions,"
            " attributes, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_GeogFromText(%s),%s,%s,%s,%s,%s,%s::jsonb,"
            "'qc_running') RETURNING dataset_id", values)
        dataset_id = cur.fetchone()[0]

    for name in rules.data_variables(ds):
        v, m = ds[name], MODEL.variables[name]
        cur.execute(
            "INSERT INTO dataset_variable (dataset_id, variable_name, source_name, units, source_units, long_name,"
            " attributes) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (dataset_id, variable_name) DO UPDATE SET"
            " source_name = EXCLUDED.source_name, units = EXCLUDED.units, source_units = EXCLUDED.source_units,"
            " long_name = EXCLUDED.long_name, attributes = EXCLUDED.attributes",
            (dataset_id, name, v.attrs.get("source_name"), m.units, v.attrs.get("source_units"), m.long_name,
             _dumps({"standard_name": m.standard_name, "valid_min": m.valid_min, "valid_max": m.valid_max})))

    obs = _observation_tuples(ds, dataset_id, path.name, overall)
    result = pgx.execute_values(
        cur,
        "INSERT INTO observation (dataset_id, platform_id, obs_time, geom, min_depth_m, max_depth_m, n_levels,"
        " source_file, source_record, overall_flag) VALUES %s"
        " ON CONFLICT (dataset_id, source_file, source_record) WHERE source_file IS NOT NULL AND source_record IS NOT NULL"
        " DO UPDATE SET platform_id = EXCLUDED.platform_id, obs_time = EXCLUDED.obs_time, geom = EXCLUDED.geom,"
        " min_depth_m = EXCLUDED.min_depth_m, max_depth_m = EXCLUDED.max_depth_m, n_levels = EXCLUDED.n_levels,"
        " overall_flag = EXCLUDED.overall_flag RETURNING observation_id, source_record",
        obs, template="(%s,%s,%s,ST_GeogFromText(%s),%s,%s,%s,%s,%s,%s)", fetch=True)
    obs_id = {rec: oid for oid, rec in result}

    flag_rows = _flag_rows(ds, report, rows, obs_id)
    cur.execute("SELECT q.observation_id, q.variable_name, q.level_index, q.flag, q.test_id FROM qc_flag_current q"
                " JOIN observation o USING (observation_id) WHERE o.dataset_id = %s AND q.set_by_user_id IS NULL",
                (dataset_id,))
    current = {(o, v, l): (f, t) for o, v, l, f, t in cur.fetchall()}
    new = [r for r in flag_rows if current.get(r[:3]) != (r[3], r[4])]
    if new:
        pgx.execute_values(cur, "INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id, detail)"
                                " VALUES %s", [(*r[:5], _dumps(r[5])) for r in new],
                           template="(%s,%s,%s,%s,%s,%s::jsonb)")

    counts, tcounts = {}, {}
    for r in flag_rows:
        counts[r[3]] = counts.get(r[3], 0) + 1
        tcounts[r[4]] = tcounts.get(r[4], 0) + 1
    reingest = existing is not None
    cur.execute("UPDATE dataset SET status = 'qc_done', updated_at = now() WHERE dataset_id = %s", (dataset_id,))
    _audit(cur, "dataset.ingest", key, None if not reingest else {"status": "qc_done"},
           {"dataset_id": dataset_id, "n_observations": len(obs), "checksum_sha256": sha, "parser": a["parser"],
            "staging": str(staged)}, "re-ingest of an existing dataset" if reingest else "first ingest")
    _audit(cur, "qc.run", key, None,
           {"config_version": cfg_version(report), "tests": report.tests_run, "flag_rows": len(flag_rows),
            "flag_rows_inserted": len(new), "flag_counts": counts, "test_counts": tcounts},
           "automatic QC; flags from earlier runs are kept as history")
    return IngestResult(key, dataset_id, a["dataset_type"], len(obs), len(flag_rows), len(new), counts, tcounts,
                        int(overall.max()) if overall.size else 0, report.config_version, str(staged), reingest)


def cfg_version(report) -> str:
    return report.config_version


def _flag_rows(ds, report, rows, obs_id) -> list[tuple]:
    """(observation_id, variable, level, flag, test_id, detail) for every expected row."""
    kind = rules.kind_of(ds)
    best = report.worst_by_cell()
    out = []
    if kind in ("profile", "timeseries"):
        for r in rows:
            idx = (r.obs, r.level) if r.level >= 0 else (r.obs,)
            f = best.get((r.variable, idx))
            if f is not None and f.flag == r.flag:
                test, detail = f.test_id, f.detail
            else:
                test, detail = "provider_flag", {"reason": "flag supplied by the data provider",
                                                 "config_version": report.config_version}
            out.append((obs_id[r.obs], r.variable, r.level, r.flag, test, detail))
    else:
        by_slice: dict = {}
        for f in report.findings:
            if f.flag in (3, 4):
                slot = by_slice.setdefault((f.variable, f.index[0] if rules.kind_of(ds) == "grid" or f.variable == "time" else 0), {})
                slot[f.test_id] = slot.get(f.test_id, 0) + 1
        for r in rows:
            tests = by_slice.get((r.variable, r.obs), {})
            test = max(tests, key=tests.get) if tests else "provider_flag"
            detail = {"summary": True, "n_cells_flag_3": r.n3, "n_cells_flag_4": r.n4, "cells_by_test": tests,
                      "config_version": report.config_version}
            out.append((obs_id[r.obs], r.variable, r.level, r.flag, test, detail))
    return out
