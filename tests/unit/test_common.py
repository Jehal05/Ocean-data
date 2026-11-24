"""Shared package: data model sync with the document, units (hand-computed), flags, validator."""
import re
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from scats_common import MODEL, combine
from scats_common import flags as F
from scats_common import units as U
from scats_common.validate import validate_dataset

ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- doc <-> yaml sync
def _doc_rows():
    text = (ROOT / "docs" / "data_model.md").read_text()
    section = text.split("## 3. Standard variables")[1].split("## 4.")[0]
    rows = {}
    for line in section.splitlines():
        m = re.match(r"\|\s*`(\w+)`\s*\|\s*`(\w+)`\s*\|\s*([^|]+?)\s*\|\s*(-?[\d.]+) to (-?[\d.]+)\s*\|", line)
        if m:
            name, std, units, lo, hi = m.groups()
            rows[name] = (std, re.sub(r"\s*\(.*\)$", "", units), float(lo), float(hi))
    return rows


def test_yaml_matches_document():
    doc = _doc_rows()
    assert len(doc) == 17, f"expected 17 variables in docs/data_model.md, parsed {len(doc)}"
    assert set(doc) == set(MODEL.variables), "variable names differ between document and YAML"
    for name, (std, units, lo, hi) in doc.items():
        v = MODEL.variables[name]
        assert (v.standard_name, v.units, v.valid_min, v.valid_max) == (std, units, lo, hi), name


def test_flag_scheme_matches_document():
    text = (ROOT / "docs" / "data_model.md").read_text().split("## 6. Quality flag scheme")[1].split("Rules:")[0]
    doc = {int(m.group(1)) for m in re.finditer(r"^\|\s*(\d)\s*\|", text, re.M)}
    assert doc == set(MODEL.flag_scheme) == set(F.FLAG_MEANINGS)


def test_flag_scheme_matches_database_schema():
    sql = (ROOT / "db" / "init" / "001_schema.sql").read_text()
    block = re.search(r"INSERT INTO qc_flag_scheme VALUES(.*?)\);\n", sql, re.S).group(1)
    db = {int(m.group(1)): m.group(2) for m in re.finditer(r"\((\d), '(\w+)'", block)}
    assert db.keys() == MODEL.flag_scheme.keys()
    assert all(db[k] == MODEL.flag_scheme[k] for k in db)


# ------------------------------------------------------------------------- units
@pytest.mark.parametrize("value,src,dst,expected", [
    (273.15, "kelvin", "degC", 0.0),
    (300.0, "K", "degC", 26.85),                  # 300 - 273.15
    (25.0, "degC", "K", 298.15),
    (18.0, "cm/s", "m s-1", 0.18),
    (10.0, "knots", "m s-1", 5.144444444444445),  # 10 * 1852 / 3600
    (1013.0, "mbar", "hPa", 1013.0),
    (35.5, "psu", "1", 35.5),
])
def test_unit_conversions_hand_computed(value, src, dst, expected):
    assert U.convert(value, src, dst) == pytest.approx(expected, rel=1e-12)


def test_unknown_units_raise():
    with pytest.raises(U.UnitError):
        U.convert(1.0, "furlong", "m")
    with pytest.raises(U.UnitError):
        U.convert(1.0, "m", "degC")


def test_elevation_to_depth_flips_sign():
    assert U.elevation_to_depth([-4000, 0, 120]).tolist() == [4000.0, -0.0, -120.0]


def test_longitude_normalisation():
    out = U.lon_to_pm180([0, 90, 180, 181, 270, 359.5, 360, 500, -30])
    assert out.tolist() == [0, 90, 180, -179, -90, -0.5, 0, 500, -30]   # 500 deliberately NOT wrapped


def test_argo_juld():
    # 75 years * 365 + 19 leap days (1952..2024) = 27394 days from 1950-01-01 to 2025-01-01
    got = U.argo_juld_to_datetime([27394.5, np.nan])
    assert str(got[0]) == "2025-01-01T12:00:00.000000000"
    assert np.isnat(got[1])


def test_iso_parse_utc_and_nat():
    got = U.parse_iso_utc(["2025-02-10T03:00:00Z", "2025-02-10T08:30:00+05:30", "garbage", None])
    assert str(got[0]) == "2025-02-10T03:00:00.000000000"
    assert str(got[1]) == "2025-02-10T03:00:00.000000000"   # +05:30 converted to UTC
    assert np.isnat(got[2]) and np.isnat(got[3])


# ------------------------------------------------------------------------- flags
def test_flag_combination_worst_wins():
    assert combine(1, 3) == 3 and combine(3, 1) == 3
    assert combine(3, 4) == 4 and combine(4, 3) == 4
    assert combine(0, 1) == 1 and combine(2, 1) == 2
    assert combine(9, 1) == 9
    assert combine(9, 3) == 3 and combine(9, 4) == 4
    assert combine(np.array([0, 1, 4]), np.array([1, 1, 1])).tolist() == [1, 1, 4]


def test_worst():
    assert F.worst([1, 1, 2]) == 2 and F.worst([1, 9, 3]) == 3 and F.worst([]) == 0


# ---------------------------------------------------------------------- validator
def _valid_timeseries() -> xr.Dataset:
    v = MODEL.variables["wind_speed"]
    ds = xr.Dataset(
        {"wind_speed": ("record", np.array([1.0, 2.0])),
         "wind_speed_qc": ("record", np.array([1, 1], dtype=np.int8)),
         "time": ("record", np.array(["2025-01-01", "2025-01-02"], dtype="datetime64[ns]")),
         "latitude": ("record", [15.0, 15.0]), "longitude": ("record", [68.0, 68.0]),
         "source_record": ("record", [0, 1]),
         "position_qc": ("record", np.array([1, 1], dtype=np.int8)),
         "time_qc": ("record", np.array([1, 1], dtype=np.int8))})
    ds["wind_speed"].attrs.update(
        standard_name=v.standard_name, long_name=v.long_name, units=v.units, source_name="w", source_units="m/s",
        valid_min=v.valid_min, valid_max=v.valid_max, ancillary_variables="wind_speed_qc",
        coordinates="time latitude longitude")
    ds["wind_speed"].encoding["_FillValue"] = np.nan
    ds.attrs.update({k: "x" for k in MODEL.required_dataset_attrs})
    ds.attrs.update(Conventions=MODEL.conventions, data_model_version=MODEL.version, platform_type="moored_buoy",
                    dataset_type="timeseries", data_mode="synthetic", processing_level="standardised",
                    is_synthetic="true")
    return ds


def test_validator_accepts_a_valid_dataset():
    assert validate_dataset(_valid_timeseries()) == []


def test_validator_reports_each_kind_of_problem():
    ds = _valid_timeseries()
    del ds.attrs["license"]
    ds.attrs["platform_type"] = "submarine"
    ds.attrs["is_synthetic"] = True
    ds["wind_speed"].attrs["units"] = "knot"
    del ds["wind_speed"].attrs["source_units"]
    ds = ds.drop_vars("wind_speed_qc")
    ds["mystery"] = ("record", [1.0, 2.0])
    problems = " | ".join(validate_dataset(ds))
    for expected in ("'license' is missing", "platform_type 'submarine'", "is_synthetic must be",
                     "units 'knot' must be 'm s-1'", "'source_units' is missing",
                     "flag variable 'wind_speed_qc' is missing", "'mystery' is not part of the data model"):
        assert expected in problems, expected
