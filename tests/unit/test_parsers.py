"""Parser unit tests: real sample files plus hand-built edge cases."""
import warnings
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from scats_common.validate import validate_dataset
from scats_parsers import ParseError, detect, parse_file, parsers

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
RAW = {p.name: p for p in (ROOT / "data/raw").rglob("*") if p.is_file() and p.suffix in (".nc", ".csv")}


def test_registry_has_five_parsers():
    assert {p.name for p in parsers()} == {"argo_netcdf", "ship_ctd_csv", "buoy_csv", "sst_grid", "bathymetry_grid"}


@pytest.mark.parametrize("name", sorted(RAW))
def test_raw_files_parse_clean(name):
    ds = parse_file(RAW[name])
    assert validate_dataset(ds) == []
    assert detect(RAW[name]).name == ds.attrs["parser"]


def test_sst_kelvin_to_celsius():
    f = next(p for n, p in RAW.items() if n.startswith("sst_"))
    src = xr.open_dataset(f, decode_times=False)
    ds = parse_file(f)
    k = float(src["analysed_sst"].isel(time=0, lat=3, lon=3))
    assert float(ds["sea_surface_temperature"].isel(time=0, latitude=3, longitude=3)) == pytest.approx(k - 273.15, abs=1e-3)


def test_bathymetry_depth_positive_and_land_flagged():
    ds = parse_file(next(p for n, p in RAW.items() if n.startswith("bathymetry")))
    d = ds["bathymetry"]
    assert float(d.min()) >= 0
    land = np.isnan(d.values)
    assert (ds["bathymetry_qc"].values[land] == 9).all()


def test_fill_values_become_nan_flag_9(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("# platform: BUOY1\n# fill_value: -999\ntime_utc,lat,lon,wspd_ms,hs_m\n"
                 "2025-01-01T00:00:00Z,15.0,70.0,5.5,1.0\n2025-01-01T01:00:00Z,15.0,70.0,-999,1.1\n")
    ds = parse_file(f)
    assert np.isnan(ds["wind_speed"].values[1])
    assert ds["wind_speed_qc"].values.tolist() == [0, 9]


def _write_csv(tmp_path, body, header="# cruise: C1\n"):
    f = tmp_path / "ctd.csv"
    f.write_text(header + "cast_id,timestamp,lat,lon,depth_m,temp_C,sal_PSU\n" + body)
    return f


def test_ctd_single_level_and_ragged(tmp_path):
    f = _write_csv(tmp_path, "A,2025-01-01T00:00:00Z,10,70,5,28,36\n"
                             "B,2025-01-02T00:00:00Z,11,71,5,27,36\nB,2025-01-02T00:00:00Z,11,71,10,26,36\n")
    ds = parse_file(f)
    assert ds.sizes["profile"] == 2 and ds["n_levels"].values.tolist() == [1, 2]
    assert validate_dataset(ds) == []


def test_ctd_all_missing_profile(tmp_path):
    f = _write_csv(tmp_path, "A,2025-01-01T00:00:00Z,10,70,5,,\nA,2025-01-01T00:00:00Z,10,70,10,,\n")
    ds = parse_file(f)
    assert np.isnan(ds["temperature"].values).all()
    assert (ds["temperature_qc"].values == 9).all()


def test_ctd_longitude_0_360_wrapped_and_nan_position(tmp_path):
    f = _write_csv(tmp_path, "A,2025-01-01T00:00:00Z,10,190,5,28,36\nB,2025-01-02T00:00:00Z,,,5,28,36\n")
    ds = parse_file(f)
    assert ds["longitude"].values[0] == pytest.approx(-170.0)
    assert ds["position_qc"].values.tolist() == [0, 9]


def test_ctd_unknown_columns_ignored_and_recorded(tmp_path):
    f = tmp_path / "ctd.csv"
    f.write_text("# cruise: C1\ncast_id,timestamp,lat,lon,depth_m,temp_C,sal_PSU,mystery\n"
                 "A,2025-01-01T00:00:00Z,10,70,5,28,36,xx\n")
    assert "mystery" in parse_file(f).attrs["ignored_columns"]


def test_unsorted_pressure_preserved_for_qc(tmp_path):
    f = _write_csv(tmp_path, "A,2025-01-01T00:00:00Z,10,70,20,28,36\nA,2025-01-01T00:00:00Z,10,70,5,28,36\n")
    d = parse_file(f)["depth"].values[0]
    assert d[0] > d[1]          # parser must not silently sort: the QC stage must see it


def test_unreadable_and_unknown_files(tmp_path):
    bad = tmp_path / "x.nc"; bad.write_bytes(b"not a netcdf")
    with pytest.raises(ParseError):
        parse_file(bad)
    with pytest.raises(ParseError):
        parse_file(tmp_path / "missing.csv")
    unk = tmp_path / "u.csv"; unk.write_text("a,b\n1,2\n")
    with pytest.raises(ParseError):
        parse_file(unk)


def test_argo_provider_flags_mapped():
    f = next(p for n, p in RAW.items() if n.startswith("argo_"))
    ds = parse_file(f)
    src = xr.open_dataset(f, decode_times=False)
    q = src["TEMP_QC"].values
    flags = ds["temperature_qc"].values
    for ch, expect in ((b"1", 1), (b"4", 4)):
        mask = q == ch
        if mask.any():
            assert (flags[mask] == expect).all()
