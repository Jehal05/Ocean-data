"""Regression tests for the Phase 2 review findings (written BEFORE the fixes)."""
import re
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from scats_parsers import ParseError, parse_file
from scats_qc import run_qc
from test_qc import T0, found, profile, series

ROOT = Path(__file__).resolve().parents[2]
H = lambda k: T0 + np.timedelta64(k, "h")          # noqa: E731


def flagged(report, test_id="timestamp_monotonic"):
    return sorted(f.index[0] for f in report.findings if f.test_id == test_id)


# ------------------------------------------------------------------ R2 time jumps
def test_forward_jump_flags_the_jumped_record_not_its_successor():
    t = [H(i) for i in range(8)]; t[3] = H(100)
    assert flagged(run_qc(series(times=t, wind_speed=[3.0] * 8))) == [3]


def test_backward_step_flags_the_stepped_record():
    t = [H(i) for i in range(8)]; t[4] = H(0) - np.timedelta64(5, "h")
    assert flagged(run_qc(series(times=t, wind_speed=[3.0] * 8))) == [4]


def test_duplicate_flags_the_later_record():
    t = [H(i) for i in range(8)]; t[3] = t[2]
    r = run_qc(series(times=t, wind_speed=[3.0] * 8))
    assert flagged(r) == [3] and found(r, "timestamp_monotonic")[0].detail["kind"] == "duplicate"


def test_jump_in_one_platform_group_does_not_touch_another():
    t = [H(0), H(100), H(2), H(3), H(1), H(2), H(3), H(4)]            # group a: record 1 jumps; group b clean
    ds = profile(pres=[[5, 10]] * 8, lat=(15,) * 8, lon=(65,) * 8, times=t, groups=["a", "a", "a", "a", "b", "b", "b", "b"])
    assert flagged(run_qc(ds)) == [1]


def test_swapped_pair_is_ambiguous_and_flags_both():
    t = [H(0), H(1), H(3), H(2), H(4), H(5)]
    r = run_qc(series(times=t, wind_speed=[3.0] * 6))
    assert flagged(r) == [2, 3] and all(f.detail["ambiguous"] for f in found(r, "timestamp_monotonic"))


def test_two_consecutive_wrong_records_are_flagged_as_ambiguous():
    t = [H(0), H(1), H(90), H(80), H(4), H(5)]
    r = run_qc(series(times=t, wind_speed=[3.0] * 6))
    assert {2, 3} <= set(flagged(r)) and all(f.detail["ambiguous"] for f in found(r, "timestamp_monotonic"))


# ------------------------------------------------------------------ R5 determinism
def test_timestamp_valid_uses_an_explicit_reference_time():
    ds = series(times=[np.datetime64("2027-01-01", "ns")] * 1 + [np.datetime64("2027-01-02", "ns")], wind_speed=[3.0, 3.0])
    assert found(run_qc(ds, reference_time="2026-11-24T00:00:00Z"), "timestamp_valid")
    assert not found(run_qc(ds, reference_time="2030-01-01T00:00:00Z"), "timestamp_valid")
    assert ds.attrs["qc_reference_time"] == "2030-01-01T00:00:00Z"


def test_qc_code_never_reads_the_clock():
    src = (ROOT / "components/03_qc_framework/scats_qc/tests.py").read_text() + \
          (ROOT / "components/03_qc_framework/scats_qc/engine.py").read_text()
    assert not re.search(r"\.now\(|utcnow|time\.time\(", src)


# ------------------------------------------------------------------ R3 synthetic is declared, never guessed
BUOY_COLS = "time_utc,lat,lon,wspd_ms,hs_m\n2025-01-01T00:00:00Z,15,68,3,1\n"


def _csv(tmp_path, head):
    f = tmp_path / "b.csv"; f.write_text(head + BUOY_COLS); return f


@pytest.mark.parametrize("head,expect", [
    ("# title: synthetic aperture radar buoy, REAL data\n", "unknown"),
    ("# SYNTHETIC DATA generated for software testing\n", "unknown"),
    ("", "unknown"),
    ("# synthetic: false\n", "false"),
    ("# synthetic: TRUE\n", "true"),
])
def test_csv_synthetic_declaration(tmp_path, head, expect):
    ds = parse_file(_csv(tmp_path, head))
    assert ds.attrs["is_synthetic"] == expect
    assert (ds.attrs["data_mode"] == "synthetic") == (expect == "true")


def test_csv_invalid_declaration_is_a_parse_error(tmp_path):
    with pytest.raises(ParseError, match="synthetic"):
        parse_file(_csv(tmp_path, "# synthetic: maybe\n"))


def test_netcdf_synthetic_declaration(tmp_path):
    src = next((ROOT / "data/raw/sst_grid").glob("*.nc"))
    for attr, expect in ((None, "unknown"), ("false", "false"), ("true", "true")):
        d = xr.open_dataset(src).load()
        d.attrs.pop("is_synthetic", None)
        d.attrs["comment"] = "SYNTHETIC DATA generated for software testing"       # the word alone must not count
        if attr:
            d.attrs["is_synthetic"] = attr
        f = tmp_path / f"s_{attr}.nc"; d.to_netcdf(f)
        assert parse_file(f).attrs["is_synthetic"] == expect


# ------------------------------------------------------------------ R7 units
def test_declared_knots_are_converted(tmp_path):
    f = tmp_path / "k.csv"; f.write_text("# units: wspd_ms=knot, hs_m=m\n" + BUOY_COLS.replace(",3,1\n", ",10,1\n"))
    ds = parse_file(f)
    assert ds["wind_speed"].values[0] == pytest.approx(10 * 1852 / 3600, abs=1e-9)        # 5.1444... m/s
    assert ds.attrs["units_assumed_from_mapping"] == "false" and "wspd_ms=knot" in ds.attrs["units_declared"]


def test_impossible_declared_unit_raises_naming_the_column(tmp_path):
    f = tmp_path / "k.csv"; f.write_text("# units: wspd_ms=kg\n" + BUOY_COLS)
    with pytest.raises(ParseError, match="wspd_ms"):
        parse_file(f)


def test_undeclared_units_are_flagged_as_assumed(tmp_path):
    ds = parse_file(_csv(tmp_path, "# synthetic: true\n"))
    assert ds.attrs["units_assumed_from_mapping"] == "true"
