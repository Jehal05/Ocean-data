"""Derived products against numbers worked out by hand (docs/phase4_design.md section 2).
Every expected value below was computed on paper from the small inputs; none comes from running the code."""
import numpy as np
import pytest
import xarray as xr

from scats_products import binning
from scats_products.config import load_config

GOOD = (1, 2)
NAN = np.nan


def profiles(position_qc=(1, 1, 4)):
    depth = np.array([[1, 2, 6, 7, 100], [1, 3, 6, 20, NAN], [1, 2, 6, 7, 100]], float)
    temp = np.array([[10, 12, 20, 22, 5], [11, 13, 21, 23, NAN], [10, 12, 20, 22, 5]], float)
    qc = np.array([[1, 2, 1, 3, 1], [1, 4, 2, 1, 9], [1, 2, 1, 3, 1]], "int8")
    return xr.Dataset({
        "temperature": (("profile", "level"), temp, {"units": "degC"}), "temperature_qc": (("profile", "level"), qc),
        "depth": (("profile", "level"), depth), "time": ("profile", np.array(["2025-01-01", "2025-01-02", "2025-01-03"], "datetime64[ns]")),
        "latitude": ("profile", [10.0, 11.0, 12.0]), "longitude": ("profile", [65.0, 66.0, 67.0]),
        "platform_id": ("profile", np.array(["A", "B", "C"], dtype=object)), "source_record": ("profile", [0, 1, 2]),
        "position_qc": ("profile", np.array(position_qc, "int8")), "time_qc": ("profile", np.array([1, 1, 1], "int8"))})


def test_binned_profile_hand_values():
    out = binning.binned_profile(profiles(), edges=[0, 5, 15, 50], variables=["temperature"], good_flags=GOOD, min_obs=1)
    t, n = out["temperature"].values, out["temperature_n_obs"].values
    # profile 0: bin [0,5) holds depth 1 (10, flag 1) and 2 (12, flag 2): mean 11, n 2.
    #            bin [5,15) holds depth 6 (20, flag 1) and 7 (22, flag 3: excluded): mean 20, n 1. depth 100 is outside every bin.
    assert t[0].tolist()[:2] == [11.0, 20.0] and n[0].tolist() == [2, 1, 0] and np.isnan(t[0, 2])
    # profile 1: depth 1 (11, flag 1) kept, depth 3 (13, flag 4) excluded -> 11, n 1; depth 6 (21, flag 2) -> 21;
    #            depth 20 (23, flag 1) is in [15,50) -> 23; the padded level (depth NaN, flag 9) is ignored.
    assert t[1].tolist() == [11.0, 21.0, 23.0] and n[1].tolist() == [1, 1, 1]


def test_a_profile_with_a_bad_position_flag_has_no_values_at_all():
    out = binning.binned_profile(profiles(), edges=[0, 5, 15, 50], variables=["temperature"], good_flags=GOOD, min_obs=1)
    assert np.isnan(out["temperature"].values[2]).all() and out["temperature_n_obs"].values[2].tolist() == [0, 0, 0]
    assert out["profile_usable"].values.tolist() == [1, 1, 0]


def test_partial_bins_are_missing_not_interpolated():
    out = binning.binned_profile(profiles(), edges=[0, 5, 15, 50], variables=["temperature"], good_flags=GOOD, min_obs=2)
    t, n = out["temperature"].values, out["temperature_n_obs"].values
    assert t[0, 0] == 11.0                                  # the one bin with two good values
    assert np.isnan(t[0, 1]) and n[0, 1] == 1               # one good value is below min_obs: missing, but still counted
    assert np.isnan(t[1]).all() and n[1].tolist() == [1, 1, 1]


def test_flags_3_4_9_never_enter_a_product():
    ds = profiles()
    ds["temperature_qc"] = (("profile", "level"), np.full((3, 5), 3, "int8"))
    out = binning.binned_profile(ds, edges=[0, 5, 15, 50], variables=["temperature"], good_flags=GOOD, min_obs=1)
    assert np.isnan(out["temperature"].values).all() and (out["temperature_n_obs"].values == 0).all()


def test_the_bin_edge_belongs_to_the_lower_bin_and_the_last_edge_is_outside():
    ds = profiles()
    ds["depth"] = (("profile", "level"), np.array([[5, 15, 50, 4.999, 0]] * 3, float))
    ds["temperature"] = (("profile", "level"), np.array([[1, 2, 3, 4, 5]] * 3, float))
    ds["temperature_qc"] = (("profile", "level"), np.ones((3, 5), "int8"))
    out = binning.binned_profile(ds, edges=[0, 5, 15, 50], variables=["temperature"], good_flags=GOOD, min_obs=1)
    # depth 5 -> [5,15); 15 -> [15,50); 50 is outside; 4.999 and 0 -> [0,5). Bin 0: values 4 and 5 -> 4.5; bin 1: 1; bin 2: 2.
    assert out["temperature"].values[0].tolist() == [4.5, 1.0, 2.0]


def series(min_check=False):
    t = np.array(["2025-01-01T00:10", "2025-01-01T00:50", "2025-01-01T01:20", "2025-01-01T03:05", "2025-01-01T00:30", "2025-01-01T01:40"], "datetime64[ns]")
    return xr.Dataset({
        "wind_speed": ("record", np.array([2, 4, 6, 8, 100, 50], float), {"units": "m s-1"}),
        "wind_speed_qc": ("record", np.array([1, 1, 1, 1, 3, 1], "int8")),
        "wind_direction": ("record", np.array([350, 10, 90, 180, 0, 0], float), {"units": "degree"}),
        "wind_direction_qc": ("record", np.array([1, 1, 1, 1, 1, 1], "int8")),
        "time": ("record", t), "latitude": ("record", [15.0] * 6), "longitude": ("record", [68.0] * 6),
        "platform_id": ("record", np.array(["B1"] * 6, dtype=object)), "source_record": ("record", np.arange(6)),
        "position_qc": ("record", np.ones(6, "int8")), "time_qc": ("record", np.array([1, 1, 1, 1, 1, 4], "int8"))})


def test_hourly_series_hand_values_and_no_interpolation_across_a_gap():
    out = binning.binned_series(series(), unit="h", good_flags=GOOD, min_obs=1, circular_units=["degree"])
    assert out["time"].values.astype("datetime64[h]").tolist() == [np.datetime64(f"2025-01-01T0{h}", "h").item() for h in range(4)]
    m, lo, hi, c = (out[f"wind_speed_{k}"].values[0] for k in ("mean", "min", "max", "count"))
    # hour 0: records 2 and 4 (record 5 has flag 3 and is excluded) -> mean 3, min 2, max 4, count 2
    # hour 1: record 6 and 50 are excluded (50 has a bad time flag) -> only 6; hour 2 has no record at all: missing, NOT 7
    # hour 3: 8
    assert m[0] == 3.0 and lo[0] == 2.0 and hi[0] == 4.0 and c.tolist() == [2, 1, 0, 1]
    assert m[1] == 6.0 and m[3] == 8.0 and np.isnan(m[2]) and np.isnan(lo[2]) and np.isnan(hi[2])


def test_daily_series_needs_enough_values_in_the_day():
    one = binning.binned_series(series(), unit="D", good_flags=GOOD, min_obs=4, circular_units=["degree"])
    assert one["wind_speed_mean"].values[0, 0] == 5.0 and one["wind_speed_count"].values[0, 0] == 4      # (2+4+6+8)/4
    two = binning.binned_series(series(), unit="D", good_flags=GOOD, min_obs=5, circular_units=["degree"])
    assert np.isnan(two["wind_speed_mean"].values[0, 0]) and two["wind_speed_count"].values[0, 0] == 4   # still counted


def test_direction_uses_a_circular_mean_and_has_no_min_max():
    out = binning.binned_series(series(), unit="h", good_flags=GOOD, min_obs=1, circular_units=["degree"])
    d = out["wind_direction_mean"].values[0, 0]                 # 350 and 10 degrees average to 0 (north), not 180
    assert min(abs(d), abs(d - 360)) < 1e-9
    assert "wind_direction_min" not in out and "wind_direction_max" not in out and out["wind_direction_count"].values[0, 0] == 3
    assert out["wind_direction_mean"].values[0, 3] == pytest.approx(180.0)


def sst():
    t = np.array(["2025-01-30", "2025-01-31", "2025-02-01", "2025-02-02"], "datetime64[ns]")
    v = np.array([[[1, 10], [5, 5]], [[2, NAN], [5, 5]], [[3, 30], [5, 5]], [[4, 40], [5, 5]]], float)
    q = np.ones((4, 2, 2), "int8")
    q[3, 0, 1] = 3
    return xr.Dataset({"sea_surface_temperature": (("time", "latitude", "longitude"), v, {"units": "degC"}),
                       "sea_surface_temperature_qc": (("time", "latitude", "longitude"), q), "time_qc": ("time", np.ones(4, "int8"))},
                      coords={"time": t, "latitude": [10.0, 10.25], "longitude": [65.0, 65.25]})


def test_monthly_mean_hand_values():
    out = binning.monthly_mean(sst(), variable="sea_surface_temperature", good_flags=GOOD, min_obs=1)
    m, n = out["sea_surface_temperature"].values, out["sea_surface_temperature_n_obs"].values
    assert out["time"].values.astype("datetime64[M]").tolist() == [np.datetime64("2025-01").item(), np.datetime64("2025-02").item()]
    # cell (0,0): January 1 and 2 -> 1.5, February 3 and 4 -> 3.5.   cell (0,1): January 10 (the NaN is not a value) -> 10,
    # February 30 (40 has flag 3 and is excluded) -> 30.   cell (1,*): all 5.
    assert m[0, 0, 0] == 1.5 and m[1, 0, 0] == 3.5 and m[0, 0, 1] == 10.0 and m[1, 0, 1] == 30.0 and (m[:, 1, :] == 5.0).all()
    assert n[:, 0, 0].tolist() == [2, 2] and n[:, 0, 1].tolist() == [1, 1]


def test_monthly_mean_partial_cells_are_missing():
    out = binning.monthly_mean(sst(), variable="sea_surface_temperature", good_flags=GOOD, min_obs=2)
    m, n = out["sea_surface_temperature"].values, out["sea_surface_temperature_n_obs"].values
    assert m[0, 0, 0] == 1.5 and np.isnan(m[0, 0, 1]) and np.isnan(m[1, 0, 1]) and n[0, 0, 1] == 1


def test_a_time_slice_with_a_bad_time_flag_is_not_used():
    ds = sst()
    ds["time_qc"] = ("time", np.array([4, 1, 1, 1], "int8"))
    out = binning.monthly_mean(ds, variable="sea_surface_temperature", good_flags=GOOD, min_obs=1)
    assert out["sea_surface_temperature"].values[0, 0, 0] == 2.0      # January keeps only the 31st


def test_products_are_deterministic_in_memory():
    from scats_store import content_sha256
    a = binning.binned_series(series(), unit="h", good_flags=GOOD, min_obs=1, circular_units=["degree"])
    b = binning.binned_series(series(), unit="h", good_flags=GOOD, min_obs=1, circular_units=["degree"])
    assert content_sha256(a) == content_sha256(b)


def test_config_is_checked():
    cfg = load_config()
    assert cfg.good_flags == (1, 2) and len(cfg.sha256) == 64
    assert cfg.raw["binned_profile"]["depth_bin_edges_m"][0] == 0
