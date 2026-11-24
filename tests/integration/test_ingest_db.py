"""Ingest against a real PostgreSQL 16 server (stand-in without PostGIS, see tests/db_stub.py)."""
import shutil
import warnings
from pathlib import Path

import psycopg2
import pytest

import db_stub
from scats_ingest import IngestConflict, ingest_file, verify_flag_consistency
from scats_ingest.staging import read_staging
from scats_parsers import ParseError, parse_file
from scats_qc import run_qc

warnings.filterwarnings("ignore")
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


@pytest.fixture
def staging(tmp_path):
    return tmp_path / "staging"


def q(sql, url=None, args=None):
    con = psycopg2.connect(url or db_stub.ADMIN_URL)
    try:
        with con, con.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else None
    finally:
        con.close()


def ingest(path, staging):
    return ingest_file(path, db_url=db_stub.INGEST_URL, staging_dir=staging)


def counts():
    return {t: q(f"SELECT count(*) FROM {t}")[0][0] for t in ("dataset", "observation", "qc_flag", "audit_log")}


def test_ingest_writes_dataset_observations_flags_and_audit(staging):
    r = ingest(ERR / "buoy_met_wave_errors.csv", staging)
    assert r.n_observations == 168 and r.flag_rows_inserted == r.flag_rows_expected == 9
    assert q("SELECT status, is_synthetic FROM dataset")[0] == ("qc_done", True)
    assert q("SELECT count(*) FROM dataset_variable")[0][0] >= 9
    assert [a[0] for a in q("SELECT action FROM audit_log ORDER BY audit_id")] == ["dataset.ingest", "qc.run"]
    assert q("SELECT audit_verify_chain()")[0][0] is None


def test_reingest_creates_no_duplicates(staging):
    ingest(ERR / "argo_profiles_errors.nc", staging)
    before = counts()
    r2 = ingest(ERR / "argo_profiles_errors.nc", staging)
    after = counts()
    assert r2.reingest and r2.flag_rows_inserted == 0
    assert (after["dataset"], after["observation"], after["qc_flag"]) == (before["dataset"], before["observation"], before["qc_flag"])
    assert after["audit_log"] == before["audit_log"] + 2          # the re-ingest itself is audited
    assert q("SELECT audit_verify_chain()")[0][0] is None


def test_flag_consistency_passes_then_detects_tampering(staging):
    r = ingest(ERR / "ctd_casts_errors.csv", staging)
    assert verify_flag_consistency(r.dataset_key, db_url=db_stub.INGEST_URL, staging_dir=staging).ok
    q("DELETE FROM qc_flag WHERE qc_flag_id = (SELECT min(qc_flag_id) FROM qc_flag)")     # admin tampers
    bad = verify_flag_consistency(r.dataset_key, db_url=db_stub.INGEST_URL, staging_dir=staging)
    assert not bad.ok and len(bad.missing_in_db) == 1
    q("UPDATE observation SET overall_flag = 0 WHERE source_record = 0")
    assert not verify_flag_consistency(r.dataset_key, db_url=db_stub.INGEST_URL, staging_dir=staging).ok


def test_staging_round_trip_keeps_flags(staging):
    ds = parse_file(ERR / "argo_profiles_errors.nc"); run_qc(ds)
    ingest(ERR / "argo_profiles_errors.nc", staging)
    back = read_staging(staging, "argo_profiles_errors")
    for name in (n for n in ds.data_vars if n.endswith("_qc")):
        assert (back[name].values == ds[name].values).all(), name


def test_same_name_different_content_is_a_conflict(staging, tmp_path):
    f = tmp_path / "b.csv"
    shutil.copy(ERR / "buoy_met_wave_errors.csv", f)
    ingest(f, staging)
    f.write_text(f.read_text() + "2025-02-01T00:00:00Z,15.0,68.0,3,40,25,70,1010,0.7,9,210,27,20,90\n")
    with pytest.raises(IngestConflict):
        ingest(f, staging)


def test_unparseable_file_writes_nothing(staging, tmp_path):
    bad = tmp_path / "x.nc"; bad.write_bytes(b"junk")
    before = counts()
    with pytest.raises(ParseError):
        ingest(bad, staging)
    assert counts() == before


def test_provider_flags_get_their_own_test_id(staging, tmp_path):
    import xarray as xr
    src = xr.open_dataset(ROOT / "data/raw/argo_profiles/argo_synthetic_arabian_sea_2025Q1.nc").load()
    qc = src["TEMP_QC"].values.copy(); qc[3, 10] = b"4"; src["TEMP_QC"].values[...] = qc
    f = tmp_path / "argo_provider.nc"; src.to_netcdf(f)
    r = ingest(f, staging)
    row = q("SELECT test_id, flag, level_index FROM qc_flag_current WHERE variable_name='temperature'")
    assert ("provider_flag", 4, 10) in row and r.flag_counts.get(4) == 1


# ------------------------------------------------------------------ least privilege
def test_ingest_role_is_not_a_superuser_and_cannot_alter_history(staging):
    ingest(ERR / "buoy_met_wave_errors.csv", staging)
    assert q("SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user",
             db_stub.INGEST_URL) == [(False, False, False)]
    for sql in ("UPDATE qc_flag SET flag = 1", "DELETE FROM qc_flag", "DELETE FROM observation",
                "UPDATE audit_log SET comment = 'x'", "ALTER TABLE audit_log DISABLE TRIGGER USER",
                "SELECT * FROM app_user", "TRUNCATE audit_log"):
        with pytest.raises(psycopg2.Error):
            q(sql, db_stub.INGEST_URL)


def test_audit_log_tamper_by_admin_is_still_blocked_by_triggers_and_chain_detects_edits(staging):
    ingest(ERR / "buoy_met_wave_errors.csv", staging)
    with pytest.raises(psycopg2.Error):
        q("UPDATE audit_log SET comment = 'x'")                 # trigger blocks even the owner
