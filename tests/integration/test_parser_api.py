"""Parser API under FastAPI TestClient, against the stand-in database."""
import importlib
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db_stub

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    db_stub.start(fresh=True)
    os.environ.update(DATABASE_URL=db_stub.INGEST_URL, STAGING_DIR=str(tmp_path_factory.mktemp("staging")),
                      INGEST_ALLOWED_DIRS=str(ROOT / "data"), INGEST_API_KEY="")
    import app.main as m
    importlib.reload(m)
    yield TestClient(m.app)
    db_stub.stop()


@pytest.fixture(autouse=True)
def clean():
    db_stub.reset_data()


def test_health_reports_limited_role(client):
    j = client.get("/health").json()
    assert j["status"] == "ok" and j["database_role"] == "scats_ingest" and j["database_role_is_superuser"] is False
    assert j["qc_tests"] == 10 and j["parsers"] == 5


def test_parsers_listing(client):
    j = client.get("/parsers").json()
    assert {p["name"] for p in j["parsers"]} == {"argo_netcdf", "ship_ctd_csv", "buoy_csv", "sst_grid", "bathymetry_grid"}
    assert all(p["variables"] for p in j["parsers"])


def test_ingest_then_qc_summary(client):
    r = client.post("/ingest", json={"path": str(ROOT / "data/test_errors/buoy_met_wave_errors.csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["dataset_key"] == "buoy_met_wave_errors" and j["flag_consistency_ok"] is True and j["status"] == "qc_done"
    s = client.get("/datasets/buoy_met_wave_errors/qc-summary").json()
    assert s["observations"] == 168 and s["persisted_flags_by_value"] == {"3": 2, "4": 5, "9": 2}
    assert set(s["persisted_flags_by_test"]) >= {"spike", "global_range", "missing_value"}
    assert s["qc_config_version"] == "0.1.0" and s["is_synthetic"] is True


def test_ingest_twice_is_idempotent(client):
    p = {"path": str(ROOT / "data/raw/ship_ctd/ctd_casts_synthetic_arabian_sea_2025.csv")}
    a, b = client.post("/ingest", json=p).json(), client.post("/ingest", json=p).json()
    assert b["reingest"] is True and b["flag_rows_inserted"] == 0 and a["dataset_id"] == b["dataset_id"]


def test_error_codes(client, tmp_path):
    assert client.post("/ingest", json={"path": "/etc/passwd"}).status_code == 403
    assert client.post("/ingest", json={"path": str(ROOT / "data/nope.csv")}).status_code == 404
    assert client.post("/ingest", json={"path": str(ROOT / "data/../README.md")}).status_code == 403
    assert client.get("/datasets/unknown/qc-summary").status_code == 404
    assert client.post("/ingest", json={}).status_code == 422


def test_unparseable_file_is_422_not_500(client, tmp_path):
    import os
    bad = ROOT / "data" / "test_errors" / "zz_bad.nc"
    bad.write_bytes(b"junk")
    try:
        assert client.post("/ingest", json={"path": str(bad)}).status_code == 422
    finally:
        os.remove(bad)


def test_api_key_when_configured(client, monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", "s3cret")
    p = {"path": str(ROOT / "data/test_errors/buoy_met_wave_errors.csv")}
    assert client.post("/ingest", json=p).status_code == 401
    assert client.post("/ingest", json=p, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/ingest", json=p, headers={"X-API-Key": "s3cret"}).status_code == 200
