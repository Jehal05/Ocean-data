"""Regression tests for the Phase 2 review findings that need PostgreSQL (written BEFORE the fixes)."""
import importlib
import os
import shutil
from pathlib import Path

import psycopg2
import pytest
from fastapi.testclient import TestClient

import db_stub
from scats_ingest import IngestConflict, ingest_file, verify_flag_consistency
from scats_ingest import service
from scats_ingest.staging import staging_path

ROOT = Path(__file__).resolve().parents[2]
ERR = ROOT / "data/test_errors"
pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")


@pytest.fixture(scope="module", autouse=True)
def server():
    db_stub.start(fresh=True)
    yield
    db_stub.stop()


@pytest.fixture(autouse=True)
def clean():
    db_stub.reset_data()


def q(sql, url=None):
    con = psycopg2.connect(url or db_stub.ADMIN_URL)
    try:
        with con, con.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall() if cur.description else None
    finally:
        con.close()


def ingest(p, st, **kw):
    return ingest_file(p, db_url=db_stub.INGEST_URL, staging_dir=st, **kw)


# ------------------------------------------------------------------ R1
def test_rejected_ingest_leaves_staging_and_database_untouched(tmp_path):
    st, f = tmp_path / "st", tmp_path / "b.csv"
    shutil.copy(ERR / "buoy_met_wave_errors.csv", f)
    r = ingest(f, st)
    before = staging_path(st, r.dataset_key).read_bytes()
    f.write_text(f.read_text() + "2030-01-01T00:00:00Z,15,68,3,40,25,70,1010,0.7,9,210,27,20,90\n")
    with pytest.raises(IngestConflict):
        ingest(f, st)
    assert staging_path(st, r.dataset_key).read_bytes() == before
    assert verify_flag_consistency(r.dataset_key, db_url=db_stub.INGEST_URL, staging_dir=st).ok
    assert not list(st.glob("*.tmp*"))


def test_database_failure_midway_leaves_staging_as_before(tmp_path, monkeypatch):
    st = tmp_path / "st"
    r = ingest(ERR / "buoy_met_wave_errors.csv", st)
    before = staging_path(st, r.dataset_key).read_bytes()
    monkeypatch.setattr(service, "_flag_rows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        ingest(ERR / "buoy_met_wave_errors.csv", st)
    assert staging_path(st, r.dataset_key).read_bytes() == before and not list(st.glob("*.tmp*"))


def test_first_ingest_that_fails_leaves_no_staging_file(tmp_path, monkeypatch):
    st = tmp_path / "st"
    monkeypatch.setattr(service, "_flag_rows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        ingest(ERR / "buoy_met_wave_errors.csv", st)
    assert not list(st.glob("*")) and q("SELECT count(*) FROM dataset")[0][0] == 0


def test_rename_failure_after_commit_is_reported_by_the_consistency_check(tmp_path, monkeypatch):
    st = tmp_path / "st"
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(service.IngestError, match="committed"):
        ingest(ERR / "buoy_met_wave_errors.csv", st)
    monkeypatch.undo()
    rep = verify_flag_consistency("buoy_met_wave_errors", db_url=db_stub.INGEST_URL, staging_dir=st)
    assert not rep.ok and "staging" in str(rep.missing_in_db)


def test_reingest_of_identical_file_is_still_a_noop(tmp_path):
    st = tmp_path / "st"
    ingest(ERR / "argo_profiles_errors.nc", st)
    b = staging_path(st, "argo_profiles_errors").read_bytes()
    r2 = ingest(ERR / "argo_profiles_errors.nc", st)
    assert r2.reingest and r2.flag_rows_inserted == 0 and staging_path(st, "argo_profiles_errors").exists()
    assert b


# ------------------------------------------------------------------ R4
def test_ingest_role_cannot_approve_or_edit_reference_data(tmp_path):
    ingest(ERR / "buoy_met_wave_errors.csv", tmp_path / "st")
    for sql in ("UPDATE dataset SET status = 'approved'", "UPDATE dataset SET status = 'published'",
                "UPDATE dataset SET title = 'x'", "UPDATE dataset SET is_synthetic = NOT is_synthetic",
                "INSERT INTO dataset (dataset_key,title,dataset_type,source,zone,storage_uri,status)"
                " VALUES ('k','t','profile','s','raw','u','approved')",
                "INSERT INTO review (dataset_id, decision) VALUES (1, 'approved')",
                "UPDATE qc_test SET severity = 'soft'", "UPDATE qc_flag_scheme SET meaning = 'x'",
                "UPDATE observation SET source_file = 'x'", "UPDATE observation SET dataset_id = 99"):
        with pytest.raises(psycopg2.Error):
            q(sql, db_stub.INGEST_URL)
    q("UPDATE dataset SET status = 'qc_done'", db_stub.INGEST_URL)          # the legitimate move still works
    r = ingest(ERR / "buoy_met_wave_errors.csv", tmp_path / "st")           # and so does a re-ingest
    assert r.reingest


# ------------------------------------------------------------------ R8
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_stub.INGEST_URL)
    monkeypatch.setenv("STAGING_DIR", str(tmp_path / "st"))
    monkeypatch.setenv("INGEST_ALLOWED_DIRS", str(ROOT / "data"))
    monkeypatch.delenv("INGEST_API_KEY", raising=False)
    monkeypatch.delenv("INGEST_ALLOW_ANONYMOUS", raising=False)
    import app.main as m
    importlib.reload(m)
    return TestClient(m.app)


BODY = {"path": str(ERR / "buoy_met_wave_errors.csv")}


def test_empty_key_disables_ingest_by_default(api):
    r = api.post("/ingest", json=BODY)
    assert r.status_code == 503 and "INGEST_API_KEY" in r.text
    assert api.get("/health").status_code == 200 and api.get("/parsers").status_code == 200


def test_anonymous_ingest_needs_an_explicit_switch(api, monkeypatch):
    monkeypatch.setenv("INGEST_ALLOW_ANONYMOUS", "true")
    assert api.post("/ingest", json=BODY).status_code == 200


def test_qc_summary_requires_the_key_when_one_is_set(api, monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", "k")
    assert api.post("/ingest", json=BODY, headers={"X-API-Key": "k"}).status_code == 200
    assert api.get("/datasets/buoy_met_wave_errors/qc-summary").status_code == 401
    j = api.get("/datasets/buoy_met_wave_errors/qc-summary", headers={"X-API-Key": "k"}).json()
    assert {"synthetic_status", "units_assumed_from_mapping", "limits"} <= set(j)


def test_compose_has_no_default_key():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "INGEST_API_KEY: ${INGEST_API_KEY:-}" in text and "INGEST_ALLOW_ANONYMOUS" in text
    assert "secrets.token_urlsafe" in (ROOT / ".env.example").read_text()
