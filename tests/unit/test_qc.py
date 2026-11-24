"""QC unit tests: each of the 10 tests has a passing, a failing and an edge case."""
import numpy as np
import pytest
import xarray as xr

from scats_common import MODEL
from scats_common.flags import GOOD, MISSING, PROBABLY_BAD, BAD
from scats_parsers.base import add_data_variable, aux_variables, initial_flags
from scats_qc import ALL_TESTS, load_config, run_qc

CFG = load_config()
T0 = np.datetime64("2025-01-01T00:00:00", "ns")


def profile(temp=None, sal=None, pres=None, lat=(15.0,), lon=(65.0,), times=None, groups=None):
    """One or more profiles. temp/sal/pres are 2-D (profile x level)."""
    pres = np.atleast_2d(np.array(pres if pres is not None else [[5, 10, 20, 50]], dtype=float))
    n, k = pres.shape
    dv = {}
    for name, vals, units in (("temperature", temp, "degC"), ("salinity", sal, "1")):
        v = np.atleast_2d(np.array(vals if vals is not None else np.full((n, k), 28.0 if name == "temperature" else 36.0), dtype=float))
        add_data_variable(dv, name, ("profile", "level"), v, initial_flags(v), "x", units, "time")
    t = np.array(times if times is not None else [T0 + np.timedelta64(i, "D") for i in range(n)], dtype="datetime64[ns]")
    dv.update(aux_variables("profile", time=t, lat=np.array(lat, float), lon=np.array(lon, float),
                            platform_id=[f"c{i}" for i in range(n)], platform_group=groups or ["g"] * n,
                            depth=pres, pressure=pres))
    return xr.Dataset(dv)


def series(times=None, **cols):
    n = len(next(iter(cols.values())))
    dv = {}
    for name, vals in cols.items():
        v = np.array(vals, dtype=float)
        add_data_variable(dv, name, ("record",), v, initial_flags(v), "x", MODEL.variables[name].units, "time")
    t = np.array(times if times is not None else [T0 + np.timedelta64(i, "h") for i in range(n)], dtype="datetime64[ns]")
    dv.update(aux_variables("record", time=t, lat=np.full(n, 15.0), lon=np.full(n, 65.0),
                            platform_id=["b"] * n, platform_group=["b"] * n))
    return xr.Dataset(dv)


def found(report, test_id, variable=None):
    return [f for f in report.findings if f.test_id == test_id and (variable is None or f.variable == variable)]


# ---------------------------------------------------------------- framework
def test_ten_tests_match_the_database_registry():
    import re
    sql = open("db/init/001_schema.sql").read()
    rows = re.findall(r"\('(\w+)',\s*'(\w+)',\s*'(hard|soft)'", sql)
    db = {r[0]: (r[1], r[2]) for r in rows if r[0] != "expert_review"}
    code = {t.test_id: (t.category, t.severity) for t in ALL_TESTS}
    assert len(ALL_TESTS) == 10 and code == db
    assert all(t.description for t in ALL_TESTS)


def test_every_threshold_has_rationale_and_source():
    for section in (CFG["regional_range"], CFG["spike"]["timeseries"], CFG["spike"]["profile"]["variables"]):
        assert all(v.get("rationale") and v.get("source") for v in section.values())


def test_detail_has_observed_threshold_and_config_version():
    ds = series(wind_speed=[5, 5, 99, 5, 5])
    r = run_qc(ds)
    f = found(r, "global_range")[0]
    assert f.detail["observed"] == 99 and f.detail["valid_max"] == 75 and f.detail["config_version"] == CFG["version"]


def test_rerun_is_idempotent():
    ds = series(wind_speed=[5, 5, 99, 5, 5], air_temperature=[25, 25, 25, 25, 80])
    r1 = run_qc(ds); first = {n: ds[n].values.copy() for n in ds.data_vars if n.endswith("_qc")}
    r2 = run_qc(ds)
    assert len(r1.findings) == len(r2.findings)
    assert all((ds[n].values == v).all() for n, v in first.items())


def test_worst_flag_wins_and_good_assigned():
    ds = series(wind_speed=[5, 5, 99, 5, 5])      # out of global range (4) and of regional range (3)
    run_qc(ds)
    assert ds["wind_speed_qc"].values.tolist() == [1, 1, 4, 1, 1]


# ---------------------------------------------------------------- 1 missing_value
def test_missing_value():
    ds = series(wind_speed=[5, np.nan, 5])
    r = run_qc(ds)
    assert [f.index for f in found(r, "missing_value", "wind_speed")] == [(1,)]
    assert ds["wind_speed_qc"].values.tolist() == [1, 9, 1]


def test_missing_value_all_missing_series_edge():
    ds = series(wind_speed=[np.nan, np.nan])
    run_qc(ds)
    assert (ds["wind_speed_qc"].values == MISSING).all()


# ---------------------------------------------------------------- 2 format_consistency
def test_format_consistency_fails_on_wrong_units():
    ds = series(wind_speed=[5, 5, 5]); ds["wind_speed"].attrs["units"] = "knot"
    assert len(found(run_qc(ds), "format_consistency")) == 3
    assert (ds["wind_speed_qc"].values == BAD).all()


def test_format_consistency_infinite_value_edge():
    ds = series(wind_speed=[5, np.inf, 5])
    assert [f.index for f in found(run_qc(ds), "format_consistency")] == [(1,)]


def test_format_consistency_passes_clean():
    assert found(run_qc(series(wind_speed=[1, 2, 3])), "format_consistency") == []


# ---------------------------------------------------------------- 3 global_range
@pytest.mark.parametrize("value,hit", [(75.0, False), (75.01, True), (0.0, False), (-0.01, True)])
def test_global_range_boundaries(value, hit):
    ds = series(wind_speed=[5, value, 5])
    assert bool(found(run_qc(ds), "global_range")) is hit


def test_global_range_skips_missing():
    assert found(run_qc(series(wind_speed=[5, np.nan, 5])), "global_range") == []


# ---------------------------------------------------------------- 4 regional_range
def test_regional_range_soft_flag_3():
    ds = series(air_temperature=[25, 25, 50, 25])        # inside global (-50..60), outside region (10..45)
    r = run_qc(ds)
    assert found(r, "global_range") == [] and len(found(r, "regional_range")) == 1
    assert ds["air_temperature_qc"].values[2] == PROBABLY_BAD


def test_regional_range_passes_inside():
    assert found(run_qc(series(air_temperature=[10, 45])), "regional_range") == []


def test_regional_range_variable_without_config_is_ignored():
    assert found(run_qc(series(wind_direction=[0, 359])), "regional_range") == []


# ---------------------------------------------------------------- 5 spike
def test_spike_profile_detects_and_flags_only_the_spike():
    temp = [[28, 28, 28 + 10, 28, 28]]
    ds = profile(temp=temp, sal=np.full((1, 5), 36.0), pres=[[5, 10, 20, 30, 40]])
    r = run_qc(ds)
    assert [f.index for f in found(r, "spike", "temperature")] == [(0, 2)]


def test_spike_profile_threshold_is_deeper_stricter():
    shallow = profile(temp=[[28, 28, 28 + 3, 28, 28]], pres=[[5, 10, 20, 30, 40]])
    deep = profile(temp=[[28, 28, 28 + 3, 28, 28]], pres=[[600, 610, 620, 630, 640]])
    assert found(run_qc(shallow), "spike") == [] and len(found(run_qc(deep), "spike", "temperature")) == 1


def test_spike_edges_not_tested_and_neighbour_missing_skipped():
    ds = profile(temp=[[40, 28, 28, np.nan, 28, 5]], sal=np.full((1, 6), 36.0), pres=[[5, 10, 20, 30, 40, 50]])
    assert found(run_qc(ds), "spike") == []


def test_spike_timeseries():
    r = run_qc(series(air_temperature=[25, 25, 25, 33, 25, 25]))
    assert [f.index for f in found(r, "spike", "air_temperature")] == [(3,)]


def test_spike_step_change_is_not_a_spike():
    assert found(run_qc(series(air_temperature=[20, 20, 20, 30, 30, 30])), "spike") == []


# ---------------------------------------------------------------- 6 position_on_land
def test_position_on_land_fails_over_land():
    ds = profile(lat=(21.0, 15.0), lon=(78.0, 65.0), pres=[[5, 10], [5, 10]])   # central India / Arabian Sea
    r = run_qc(ds)
    assert [f.index for f in found(r, "position_on_land")] == [(0,)]
    assert ds["position_qc"].values.tolist() == [BAD, GOOD]


def test_position_on_land_missing_position_edge():
    ds = profile(lat=(np.nan,), lon=(np.nan,))
    assert found(run_qc(ds), "position_on_land") == [] and ds["position_qc"].values[0] == MISSING


# ---------------------------------------------------------------- 7 position_range
def test_position_range_fails_outside_limits():
    ds = profile(lat=(95.0,), lon=(65.0,))
    r = run_qc(ds)
    assert len(found(r, "position_range")) == 1 and found(r, "position_on_land") == []
    assert ds["position_qc"].values[0] == BAD


def test_position_range_boundary_ok():
    assert found(run_qc(profile(lat=(90.0,), lon=(180.0,))), "position_range") == []


# ---------------------------------------------------------------- 8 timestamp_valid
def test_timestamp_valid_rejects_future_and_ancient():
    ds = profile(pres=[[5, 10]] * 3, lat=(15,) * 3, lon=(65,) * 3,
                 times=[np.datetime64("2090-01-01", "ns"), np.datetime64("1900-01-01", "ns"), T0])
    r = run_qc(ds, reference_time="2026-11-24T00:00:00Z")     # was clock-based; the reference time is now an explicit input
    assert sorted(f.index[0] for f in found(r, "timestamp_valid")) == [0, 1]


def test_timestamp_valid_missing_time_edge():
    ds = profile(times=[np.datetime64("NaT", "ns")])
    assert found(run_qc(ds), "timestamp_valid") == [] and ds["time_qc"].values[0] == MISSING


# ---------------------------------------------------------------- 9 timestamp_monotonic
def test_timestamp_monotonic_duplicate_and_decrease_flag_later_record():
    t = [T0, T0 + np.timedelta64(1, "D"), T0 + np.timedelta64(1, "D"), T0 + np.timedelta64(3, "D"), T0 + np.timedelta64(2, "D")]
    ds = profile(pres=[[5, 10]] * 5, lat=(15,) * 5, lon=(65,) * 5, times=t)
    r = run_qc(ds)
    # records 3 (3 d) and 4 (2 d) are a swapped pair: both fit between their outer neighbours, so the test cannot say
    # which is wrong and flags both as ambiguous (old rule blamed record 4). Resolvable cases: tests/unit/test_review_fixes.py
    assert {(f.index[0], f.detail["kind"]) for f in found(r, "timestamp_monotonic")} == {
        (2, "duplicate"), (3, "out_of_order"), (4, "out_of_order")}


def test_timestamp_monotonic_groups_are_independent():
    t = [T0 + np.timedelta64(5, "D"), T0, T0 + np.timedelta64(6, "D"), T0 + np.timedelta64(1, "D")]
    ds = profile(pres=[[5, 10]] * 4, lat=(15,) * 4, lon=(65,) * 4, times=t, groups=["a", "b", "a", "b"])
    assert found(run_qc(ds), "timestamp_monotonic") == []


def test_timestamp_monotonic_single_record_edge():
    assert found(run_qc(profile()), "timestamp_monotonic") == []


# ---------------------------------------------------------------- 10 density_inversion
def test_density_inversion_flags_both_levels_of_the_pair():
    temp = [[28.0, 28.0, 33.0, 28.0]]            # warm layer at 20 dbar under cooler water above: lighter below
    ds = profile(temp=temp, pres=[[5, 10, 20, 50]])
    r = run_qc(ds)
    idx = {f.index for f in found(r, "density_inversion", "temperature")}
    assert (0, 1) in idx and (0, 2) in idx


def test_density_inversion_stable_column_passes():
    ds = profile(temp=[[29, 28, 27, 26]], pres=[[5, 10, 20, 50]])
    assert found(run_qc(ds), "density_inversion") == []


def test_density_inversion_within_tolerance_and_missing_edge():
    ds = profile(temp=[[28.0, np.nan, 28.0, 28.0]], pres=[[5, 10, 20, 50]])
    assert found(run_qc(ds), "density_inversion") == []
