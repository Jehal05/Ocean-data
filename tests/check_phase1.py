#!/usr/bin/env python3
"""Phase 1 acceptance check. Prints PASS / FAIL / SKIP with evidence.

SKIP means "could not be verified here", never "passed". Exit code is 1 if any
check FAILs, 0 otherwise (read the SKIPs).

Environment (defaults match docker-compose.yml):
  API_URL        http://localhost:8000
  DATABASE_URL   postgresql://scats_app:scats_app_dev_pw@localhost:5432/scats
  S3_ENDPOINT    http://localhost:8333   S3_ACCESS_KEY / S3_SECRET_KEY
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_URL = os.getenv("API_URL", "http://localhost:8000")
DB_URL = os.getenv("DATABASE_URL", "postgresql://scats_app:scats_app_dev_pw@localhost:5432/scats")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:8333")
S3_KEY = os.getenv("S3_ACCESS_KEY", "scatsdev")
S3_SECRET = os.getenv("S3_SECRET_KEY", "scatsdev_secret_pw")

results: list[tuple[str, str, str]] = []


def record(name, status, evidence):
    results.append((name, status, evidence))


def s3():
    import boto3
    from botocore.client import Config
    return boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET,
                        region_name="us-east-1", config=Config(signature_version="s3v4", connect_timeout=3,
                                                              retries={"max_attempts": 1}))


# 1. one-command startup -------------------------------------------------------
def check_compose():
    name = "docker compose up starts all services with one command"
    compose = ROOT / "docker-compose.yml"
    if not shutil.which("docker"):
        return record(name, "SKIP", "docker not installed here; compose file present: " + str(compose.exists()))
    cfg = subprocess.run(["docker", "compose", "-f", str(compose), "config", "--services"],
                         capture_output=True, text=True, cwd=ROOT)
    if cfg.returncode != 0:
        return record(name, "FAIL", "compose file invalid: " + cfg.stderr.strip()[:200])
    services = set(cfg.stdout.split())
    expected = {"db", "storage", "storage-init", "api"}
    if not expected <= services:
        return record(name, "FAIL", f"missing services {expected - services}")
    ps = subprocess.run(["docker", "compose", "-f", str(compose), "ps", "--format", "json"],
                        capture_output=True, text=True, cwd=ROOT)
    if ps.returncode != 0 or not ps.stdout.strip():
        return record(name, "SKIP", f"compose file valid, services {sorted(services)}; "
                                    "stack is not running or Docker daemon unreachable, so 'up' not verified")
    rows = [json.loads(line) for line in ps.stdout.splitlines() if line.strip()]
    running = {r["Service"] for r in rows if r.get("State") == "running"}
    init_ok = any(r["Service"] == "storage-init" and r.get("State") == "exited" and r.get("ExitCode") == 0 for r in rows)
    ok = {"db", "storage", "api"} <= running and init_ok
    record(name, "PASS" if ok else "FAIL", f"running={sorted(running)} storage-init exited 0: {init_ok}")


# 2. health endpoint -----------------------------------------------------------
def check_health():
    name = "The health endpoint responds"
    try:
        with urllib.request.urlopen(API_URL + "/health", timeout=5) as r:
            body = json.load(r)
        record(name, "PASS" if body.get("status") == "ok" else "FAIL",
               f"GET /health -> status={body.get('status')} db_ok={body['database']['ok']} storage_ok={body['storage']['ok']}")
    except Exception as exc:  # noqa: BLE001
        record(name, "FAIL", f"{API_URL}/health unreachable: {str(exc)[:120]}")


# 3. database schema and audit log --------------------------------------------
def check_schema():
    name = "The database schema is created, including the audit log"
    try:
        import psycopg2
        conn = psycopg2.connect(DB_URL, connect_timeout=3)
    except Exception as exc:  # noqa: BLE001
        return record(name, "FAIL", f"cannot connect: {str(exc)[:120]}")
    try:
        cur = conn.cursor()
        need = {"storage_zone", "qc_flag_scheme", "qc_test", "app_role", "app_user", "dataset", "dataset_variable",
                "observation", "qc_flag", "review", "audit_log"}
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        have = {r[0] for r in cur.fetchall()}
        missing = need - have
        cur.execute("SELECT count(*) FROM audit_log")
        n_before = cur.fetchone()[0]
        blocked = {}
        for label, sql in (("UPDATE", "UPDATE audit_log SET action='x'"), ("DELETE", "DELETE FROM audit_log"),
                           ("TRUNCATE", "TRUNCATE audit_log")):
            try:
                cur.execute(sql)
                blocked[label] = False
            except Exception:  # noqa: BLE001
                blocked[label] = True
            conn.rollback()
        cur.execute("SELECT audit_verify_chain()")
        chain = cur.fetchone()[0]
        cur.execute("SELECT audit_verify_recent(10)")
        recent = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM qc_flag_scheme")
        n_flags = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_indexes WHERE indexname = 'observation_source_uq'")
        has_unique = cur.fetchone()[0] == 1
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        is_super = cur.fetchone()[0]
        ok = (not missing and all(blocked.values()) and chain is None and recent is None and n_before >= 1
              and n_flags == 8 and has_unique and not is_super)
        record(name, "PASS" if ok else "FAIL",
               f"tables missing={sorted(missing) or 'none'}; audit rows={n_before}; "
               f"UPDATE/DELETE/TRUNCATE blocked={blocked}; full chain intact={chain is None}; recent intact={recent is None}; "
               f"flag values={n_flags}; observation unique index={has_unique}; connected as superuser={is_super}")
    finally:
        conn.close()


# 4. storage zones -------------------------------------------------------------
def check_zones():
    name = "The three storage zones exist"
    try:
        have = {b["Name"] for b in s3().list_buckets()["Buckets"]}
    except Exception as exc:  # noqa: BLE001
        return record(name, "FAIL", f"object storage unreachable: {str(exc)[:120]}")
    missing = {"raw", "standardised", "derived"} - have
    record(name, "FAIL" if missing else "PASS", f"buckets present: {sorted(have)}; missing: {sorted(missing) or 'none'}")


# 5. data model spec -----------------------------------------------------------
def check_data_model():
    name = "The data model spec is complete and lists open questions"
    p = ROOT / "docs" / "data_model.md"
    if not p.exists():
        return record(name, "FAIL", "docs/data_model.md missing")
    text = p.read_text()
    needs = ["## 2. Coordinates", "## 3. Standard variables", "## 4. Controlled vocabularies", "## 5. Required metadata",
             "## 6. Quality flag scheme", "## 8. Open questions for NPOL"]
    missing = [h for h in needs if h not in text]
    questions = len(re.findall(r"^\d+\. ", text.split("## 8. Open questions for NPOL")[-1].split("## 9.")[0], re.M))
    required_vars = ["temperature", "salinity", "sound_velocity", "density", "current_speed", "current_direction",
                     "wind_speed", "wind_direction", "relative_humidity", "air_temperature", "wave_height",
                     "wave_direction", "wave_period", "bathymetry", "sea_level_anomaly", "sea_surface_temperature"]
    absent = [v for v in required_vars if f"`{v}`" not in text]
    ok = not missing and not absent and questions >= 5
    record(name, "PASS" if ok else "FAIL",
           f"sections missing={missing or 'none'}; variables missing={absent or 'none'}; open questions={questions}")


# 6. sample data ---------------------------------------------------------------
def check_sample_data():
    name = "Sample data from 4 dataset types is in the raw zone"
    folders = {"profiles (Argo-style)": "argo_profiles", "profiles (ship CTD)": "ship_ctd",
               "gridded SST": "sst_grid", "wind/wave/met": "buoy_met_wave", "bathymetry": "bathymetry"}
    local = {k: [f.name for f in (ROOT / "data" / "raw" / v).glob("*") if f.is_file() and f.name != "MANIFEST.sha256"]
             for k, v in folders.items()}
    empty = [k for k, v in local.items() if not v]
    formats = {Path(f).suffix for v in local.values() for f in v}
    try:
        keys = {o["Key"] for o in s3().list_objects_v2(Bucket="raw").get("Contents", [])}
        in_bucket = sum(1 for v in folders.values() for k in keys if k.startswith(v + "/"))
        bucket_note = f"{in_bucket} data objects in raw bucket"
        bucket_ok = in_bucket >= 5
    except Exception as exc:  # noqa: BLE001
        bucket_note, bucket_ok = f"raw bucket not checked ({str(exc)[:60]})", None
    ok_local = not empty and len(formats) >= 2
    note = f"local files for all types={not empty} (missing: {empty or 'none'}); formats={sorted(formats)}; {bucket_note}"
    if not ok_local or bucket_ok is False:
        record(name, "FAIL", note)
    elif bucket_ok is None:
        record(name, "SKIP", "files exist locally but could not confirm upload to raw zone: " + note)
    else:
        record(name, "PASS", note)


# 7. error-injected set --------------------------------------------------------
def check_error_set():
    name = "The error-injected set exists with its answer key"
    d = ROOT / "data" / "test_errors"
    key = d / "answer_key.csv"
    if not key.exists():
        return record(name, "FAIL", "answer_key.csv missing")
    import csv
    rows = list(csv.DictReader(open(key)))
    cols = {"error_id", "file", "record_kind", "record_index", "level_index", "cast_id", "depth_m", "variable",
            "error_type", "original_value", "injected_value", "expected_primary_test", "notes"}
    if not cols <= set(rows[0]):
        return record(name, "FAIL", f"answer key columns missing: {sorted(cols - set(rows[0]))}")
    bad_idx = [r["error_id"] for r in rows if not r["record_index"].isdigit()
               or r["record_kind"] not in ("profile_index", "csv_data_row")]
    if bad_idx:
        return record(name, "FAIL", f"bad record_index/record_kind in rows {bad_idx}")
    types = {r["error_type"] for r in rows}
    need = {"spike", "out_of_range", "position_on_land", "timestamp_duplicate", "timestamp_non_monotonic", "missing",
            "density_inversion"}
    files = {r["file"] for r in rows}
    absent = [f for f in files if not (d / f).exists()]
    ok = need <= types and not absent
    record(name, "PASS" if ok else "FAIL",
           f"{len(rows)} injected errors; types={sorted(types)}; required types missing={sorted(need - types) or 'none'}; "
           f"files missing={absent or 'none'}")


# 8. architecture doc ----------------------------------------------------------
def check_architecture():
    name = "The architecture document is written"
    p = ROOT / "docs" / "architecture.md"
    if not p.exists():
        return record(name, "FAIL", "docs/architecture.md missing")
    t = p.read_text()
    comps = ["Data Sources", "Data Parser API", "QC Framework", "Expert Review Interface", "Scientific Data Store",
             "Derived Products Pipeline", "Data Access API", "Visualisation Dashboard"]
    absent = [c for c in comps if c not in t]
    ok = "```mermaid" in t and not absent and "Technology choices" in t and "Scope" in t
    record(name, "PASS" if ok else "FAIL", f"diagram present={'```mermaid' in t}; components missing={absent or 'none'}")


def main() -> int:
    for fn in (check_compose, check_health, check_schema, check_zones, check_data_model, check_sample_data,
               check_error_set, check_architecture):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record(fn.__name__, "FAIL", f"check crashed: {exc}")
    for name, status, ev in results:
        print(f"[{status:4s}] {name}\n        {ev}")
    counts = {s: sum(1 for r in results if r[1] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped (not verified)")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
