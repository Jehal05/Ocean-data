"""SCATS POC: Data Access API skeleton (Phase 1).

Phase 1 only provides health checks and a read-only catalogue listing, so the
stack can be proven end to end. Real endpoints (subsetting, profiles,
products) arrive in Phase 4.
"""
from __future__ import annotations

import os

import boto3
import psycopg2
from botocore.client import Config
from fastapi import FastAPI

app = FastAPI(
    title="SCATS Ocean Data Platform: Data Access API",
    version="0.1.1-phase1",
    description="Phase 1 skeleton: health and catalogue only.",
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://scats_app:scats_app_dev_pw@localhost:5432/scats")
HEALTH_AUDIT_WINDOW = int(os.getenv("HEALTH_AUDIT_WINDOW", "1000"))  # newest rows checked by /health
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:8333")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "scatsdev")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "scatsdev_secret_pw")
ZONE_BUCKETS = [b for b in os.getenv("ZONE_BUCKETS", "raw,standardised,derived").split(",") if b]


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=3,
                      retries={"max_attempts": 1}),
    )


def check_database(full_audit: bool = False) -> dict:
    """full_audit=False checks only the newest rows (cheap); True walks the whole audit log."""
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        try:
            with conn.cursor() as cur:
                # pg_tables lists every table, even ones this limited role cannot read
                # (information_schema.tables only shows tables the role has privileges on).
                cur.execute(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN "
                    "('dataset','observation','qc_flag','app_user','audit_log')"
                )
                core_tables = cur.fetchone()[0]
                if full_audit:
                    cur.execute("SELECT audit_verify_chain()")
                else:
                    cur.execute("SELECT audit_verify_recent(%s)", (HEALTH_AUDIT_WINDOW,))
                broken_at = cur.fetchone()[0]
        finally:
            conn.close()
        return {"ok": core_tables == 5 and broken_at is None,
                "core_tables_found": core_tables, "audit_chain_broken_at": broken_at}
    except Exception as exc:  # noqa: BLE001 - health check must never raise
        return {"ok": False, "error": str(exc)[:200]}


def check_storage() -> dict:
    try:
        existing = {b["Name"] for b in _s3().list_buckets().get("Buckets", [])}
        missing = [b for b in ZONE_BUCKETS if b not in existing]
        return {"ok": not missing, "zones": {b: b in existing for b in ZONE_BUCKETS},
                "missing": missing}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


@app.get("/health/live", tags=["health"])
def live() -> dict:
    """Process is up. Does not touch dependencies."""
    return {"status": "ok"}


@app.get("/health", tags=["health"])
def health() -> dict:
    """Process is up and can reach the database and object storage."""
    db, storage = check_database(), check_storage()
    return {
        "status": "ok" if db["ok"] and storage["ok"] else "degraded",
        "service": "scats-data-access-api",
        "version": app.version,
        "database": db,
        "storage": storage,
    }


@app.get("/health/audit", tags=["health"])
def health_audit() -> dict:
    """Verify the ENTIRE audit-log hash chain (slow on a large log; use sparingly)."""
    db = check_database(full_audit=True)
    return {"status": "ok" if db["ok"] else "broken", "database": db}


@app.get("/catalogue", tags=["catalogue"])
def catalogue() -> dict:
    """Read-only list of datasets (empty until Phase 2 ingests data)."""
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dataset_key, title, dataset_type, source, is_synthetic, zone, status "
                "FROM dataset ORDER BY dataset_key"
            )
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"count": len(rows), "datasets": rows}
