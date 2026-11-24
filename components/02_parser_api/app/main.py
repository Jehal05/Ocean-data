"""Parser API (Phase 2): list parsers, ingest a file, read a dataset's QC summary.

Runs as the least-privilege database role scats_ingest, never as the superuser.
POST /ingest changes data. It is bound to localhost by docker-compose and, when INGEST_API_KEY is set,
requires the header X-API-Key. Real authentication and roles arrive with the review UI (Phase 3).
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path

import psycopg2
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from scats_ingest import IngestConflict, ingest_file, verify_flag_consistency
from scats_parsers import ParseError, parsers
from scats_parsers.base import __version__ as PARSER_VERSION
from scats_qc import ALL_TESTS, load_config

app = FastAPI(title="SCATS Parser API", version="0.2.0")


def settings() -> dict:
    return {
        "db_url": os.getenv("DATABASE_URL", "postgresql://scats_ingest:scats_ingest_dev_pw@localhost:5432/scats"),
        "staging_dir": Path(os.getenv("STAGING_DIR", "/srv/staging")),
        "allowed_dirs": [Path(p).resolve() for p in os.getenv("INGEST_ALLOWED_DIRS", "/srv/data").split(os.pathsep) if p],
        "api_key": os.getenv("INGEST_API_KEY", ""),
    }


def require_key(x_api_key: str | None = Header(default=None)):
    key = settings()["api_key"]
    if key and not hmac.compare_digest(key, x_api_key or ""):
        raise HTTPException(401, "missing or wrong X-API-Key")


def resolve_allowed(raw: str) -> Path:
    """Resolve a requested path and refuse anything outside the allowed directories (symlinks and .. included)."""
    p = Path(raw).resolve()
    if not any(p.is_relative_to(d) for d in settings()["allowed_dirs"]):
        raise HTTPException(403, "path is outside the allowed ingest directories")
    if not p.is_file():
        raise HTTPException(404, "file not found")
    return p


@app.get("/health")
def health():
    try:
        con = psycopg2.connect(settings()["db_url"], connect_timeout=3)
        with con, con.cursor() as cur:
            cur.execute("SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)")
            user, superuser = cur.fetchone()
        con.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "database": f"unreachable: {type(exc).__name__}"}
    status = "ok" if not superuser else "degraded"
    return {"status": status, "database_role": user, "database_role_is_superuser": superuser,
            "parsers": len(parsers()), "qc_tests": len(ALL_TESTS), "qc_config_version": load_config()["version"],
            "parser_version": PARSER_VERSION}


@app.get("/parsers")
def list_parsers():
    out = []
    for cls in parsers():
        m = cls.mapping()
        out.append({"name": cls.name, "source_type": m["source_type"], "platform_type": m["platform_type"],
                    "dataset_type": m["dataset_type"], "mapping_file": cls.mapping_file,
                    "variables": sorted({v["name"] for v in m["variables"].values()})})
    return {"parser_version": PARSER_VERSION, "parsers": out}


class IngestRequest(BaseModel):
    path: str


@app.post("/ingest", dependencies=[Depends(require_key)])
def ingest(req: IngestRequest):
    s = settings()
    path = resolve_allowed(req.path)
    try:
        result = ingest_file(path, db_url=s["db_url"], staging_dir=s["staging_dir"])
    except ParseError as exc:
        raise HTTPException(422, f"cannot parse file: {exc}")
    except IngestConflict as exc:
        raise HTTPException(409, str(exc))
    except psycopg2.OperationalError:
        raise HTTPException(503, "database unavailable")
    check = verify_flag_consistency(result.dataset_key, db_url=s["db_url"], staging_dir=s["staging_dir"])
    return {**result.__dict__, "flag_consistency_ok": check.ok}


@app.get("/datasets/{dataset_key}/qc-summary")
def qc_summary(dataset_key: str):
    try:
        con = psycopg2.connect(settings()["db_url"], connect_timeout=3)
    except psycopg2.OperationalError:
        raise HTTPException(503, "database unavailable")
    try:
        with con, con.cursor() as cur:
            cur.execute("SELECT dataset_id, dataset_type, status, is_synthetic FROM dataset WHERE dataset_key = %s",
                        (dataset_key,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, f"dataset '{dataset_key}' not found")
            did, dtype, status, synthetic = row
            cur.execute("SELECT count(*), COALESCE(max(overall_flag), 0) FROM observation WHERE dataset_id = %s", (did,))
            n_obs, worst = cur.fetchone()
            cur.execute("SELECT overall_flag, count(*) FROM observation WHERE dataset_id = %s GROUP BY 1 ORDER BY 1", (did,))
            overall = {int(f): n for f, n in cur.fetchall()}
            base = ("FROM qc_flag_current q JOIN observation o USING (observation_id) "
                    "WHERE o.dataset_id = %s AND q.flag IN (2,3,4,5,8,9)")
            cur.execute(f"SELECT q.flag, count(*) {base} GROUP BY 1 ORDER BY 1", (did,))
            by_flag = {int(f): n for f, n in cur.fetchall()}
            cur.execute(f"SELECT q.test_id, count(*) {base} GROUP BY 1 ORDER BY 2 DESC", (did,))
            by_test = dict(cur.fetchall())
            cur.execute(f"SELECT q.variable_name, count(*) {base} GROUP BY 1 ORDER BY 2 DESC", (did,))
            by_var = dict(cur.fetchall())
            cur.execute("SELECT after_state->>'config_version' FROM audit_log WHERE action = 'qc.run' AND entity_id = %s"
                        " ORDER BY audit_id DESC LIMIT 1", (dataset_key,))
            cfg = cur.fetchone()
    finally:
        con.close()
    return {"dataset_key": dataset_key, "dataset_type": dtype, "status": status, "is_synthetic": synthetic,
            "observations": n_obs, "worst_overall_flag": int(worst), "observations_by_overall_flag": overall,
            "persisted_flags_by_value": by_flag, "persisted_flags_by_test": by_test,
            "persisted_flags_by_variable": by_var, "qc_config_version": cfg[0] if cfg else None,
            "note": "flags 0 and 1 are not stored per cell; grids store summary rows (see docs/phase2_design.md)"}
