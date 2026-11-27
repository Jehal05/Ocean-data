"""The whole realistic set through the real ingest + QC, scored against its ground truth (tests/score_realistic.py).
Faults the framework has no test for (stuck values, drift, an offset) are listed in the key as not_covered: they are reported, never counted as misses."""
import warnings

import pytest

import db_stub
import score_realistic as R

warnings.filterwarnings("ignore")
pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")


@pytest.fixture(scope="module")
def result():
    db_stub.start(fresh=True)
    try:
        yield R.score(db_stub.INGEST_URL, db_stub)
    finally:
        db_stub.stop()


def test_every_covered_fault_is_flagged_3_4_or_9(result):
    assert result["misses"] == [], result["misses"]
    n, d = (int(x) for x in result["recall"].split("/"))
    assert n == d and d >= 80


def test_the_uncovered_faults_are_reported_honestly(result):
    assert set(result["not_covered_by_qc"]) == {"stuck_value", "salinity_drift", "pressure_offset", "sensor_drift"}


def test_flags_by_value_include_the_provider_codes_and_every_dataset_loaded(result):
    assert len(result["observations"]) == 16
    argo = [v for k, v in result["flags_by_value"].items() if k.startswith("argo_")]
    assert all({2, 3, 4, 9} <= set(v) for v in argo)


def test_natural_false_positives_stay_small(result):
    fp = result["unexpected_flag_3_4_by_test"]
    assert fp.get("spike", 0) + fp.get("global_range", 0) + fp.get("regional_range", 0) <= 30, fp   # clean physics must not trip the value tests
    assert fp.get("density_inversion", 0) < 400 and fp.get("timestamp_monotonic", 0) < 200, fp


def test_ingest_is_fast_enough(result):
    assert max(result["ingest_seconds"].values()) < 120 and sum(result["ingest_seconds"].values()) < 300
