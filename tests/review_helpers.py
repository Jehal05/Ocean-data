"""Shared helpers for the Phase 3 tests: a review client on the stand-in database and moto S3."""
from __future__ import annotations

from pathlib import Path

import boto3
import psycopg2

import db_stub

ROOT = Path(__file__).resolve().parents[1]
ERR = ROOT / "data/test_errors"
RAW = ROOT / "data/raw"
BUOY = ERR / "buoy_met_wave_errors.csv"
CTD = ERR / "ctd_casts_errors.csv"
BUOY_KEY, CTD_KEY = "buoy_met_wave_errors", "ctd_casts_errors"
NEW_PASSWORD = "a-long-test-password-1"


def sql(query, params=None, url=None, fetch=True):
    con = psycopg2.connect(url or db_stub.ADMIN_URL)
    try:
        with con, con.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall() if (fetch and cur.description) else None
    finally:
        con.close()


def set_env(monkeypatch, staging: Path, **extra):
    monkeypatch.setenv("DATABASE_URL", db_stub.REVIEW_URL)
    monkeypatch.setenv("STAGING_DIR", str(staging))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    for k, v in extra.items():
        monkeypatch.setenv(k, str(v))


def make_bucket():
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="standardised")


def ingest(path, staging, actor="scats_ingest", **kw):
    from scats_ingest import ingest_file
    return ingest_file(path, db_url=db_stub.INGEST_URL, staging_dir=staging, actor=actor, **kw)


def login(client, username, password=None):
    password = password or f"{username}123"
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.cookies.clear()                              # tests use the Bearer header; cookies have their own tests
    return {"Authorization": "Bearer " + r.json()["token"]}


def add_user(client, admin_h, username, role, password=NEW_PASSWORD):
    r = client.post("/admin/users", headers=admin_h, json={"username": username, "display_name": username, "password": password, "role": role})
    assert r.status_code == 201, r.text
    return password


def flagged(client, h, key, flag=4, page_size=200, **q):
    params = {"flag": flag, "page_size": page_size, **q}
    r = client.get(f"/datasets/{key}/flags", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()["flags"]


def cell_of(f):
    return {"observation_id": f["observation_id"], "variable": f["variable"], "level_index": f["level_index"]}


def to_in_review(client, h, key):
    r = client.post(f"/datasets/{key}/review/start", headers=h)
    assert r.status_code == 200, r.text
