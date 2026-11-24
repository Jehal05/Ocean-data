"""Stand-in PostgreSQL for machines without Docker (this sandbox).

It starts a plain PostgreSQL 16 cluster and applies db/init/*.sql with PostGIS taken out:
geography columns become text, GIST indexes become btree, and ST_GeogFromText() is a shim that returns
its argument. EVERYTHING ELSE (tables, constraints, triggers, roles, grants, audit chain) is the real SQL.
What this does NOT prove: that the geography columns and spatial indexes work in real PostGIS.

Used by tests only. docker compose uses the real postgis image and never touches this file.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
BIN = next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin"))[::-1]), None)
PORT = int(os.getenv("SCATS_STUB_PORT", "55432"))
DATA = Path(os.getenv("SCATS_STUB_DIR", "/tmp/scats_pg_stub"))
DB = "scats"
INGEST_URL = f"postgresql://scats_ingest:scats_ingest_dev_pw@127.0.0.1:{PORT}/{DB}"
APP_URL = f"postgresql://scats_app:scats_app_dev_pw@127.0.0.1:{PORT}/{DB}"
ADMIN_URL = f"postgresql://pgt@127.0.0.1:{PORT}/{DB}"


def available() -> bool:
    return BIN is not None and (BIN / "initdb").exists() and shutil.which("su") is not None


def stub_sql(text: str) -> str:
    text = re.sub(r"CREATE EXTENSION IF NOT EXISTS postgis;", "", text)
    text = re.sub(r"geography\(\w+,\s*\d+\)", "text", text)
    text = re.sub(r"USING GIST \((\w+)\)", r"(\1)", text)
    return text


def _run(cmd: str, check=True):
    return subprocess.run(["su", "pgt", "-c", cmd], capture_output=True, text=True, check=check)


def start(fresh: bool = True) -> str:
    """Start the stand-in server and load the schema. Returns the superuser DSN."""
    if not available():
        raise RuntimeError("no local PostgreSQL binaries")
    stop()
    if fresh and DATA.exists():
        shutil.rmtree(DATA)
    if not DATA.exists():
        DATA.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["useradd", "-m", "pgt"], capture_output=True)
        DATA.mkdir()
        shutil.chown(DATA, "pgt")
        _run(f"{BIN}/initdb -D {DATA} -A trust -E UTF8")
    _run(f"{BIN}/pg_ctl -D {DATA} -o '-p {PORT} -k /tmp -c listen_addresses=127.0.0.1' -l {DATA}.log -w start")
    _run(f"{BIN}/createdb -h 127.0.0.1 -p {PORT} {DB}", check=False)
    con = psycopg2.connect(ADMIN_URL)
    con.autocommit = True
    cur = con.cursor()
    cur.execute("SELECT to_regclass('public.dataset')")
    if cur.fetchone()[0] is None:
        cur.execute("CREATE OR REPLACE FUNCTION ST_GeogFromText(t text) RETURNS text AS 'SELECT t' LANGUAGE sql IMMUTABLE")
        for f in sorted((ROOT / "db" / "init").glob("*.sql")):
            cur.execute(stub_sql(f.read_text()))
    con.close()
    return ADMIN_URL


def stop():
    if DATA.exists():
        _run(f"{BIN}/pg_ctl -D {DATA} -m immediate stop", check=False)


def reset_data():
    """Empty the data tables between tests (superuser only; the audit triggers are bypassed for the stand-in)."""
    con = psycopg2.connect(ADMIN_URL)
    con.autocommit = True
    cur = con.cursor()
    cur.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
    cur.execute("TRUNCATE qc_flag, observation, dataset_variable, dataset, audit_log RESTART IDENTITY CASCADE")
    cur.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")
    con.close()
