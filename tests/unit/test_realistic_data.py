"""Safeguards of the REALISTIC synthetic profile: deterministic, honestly labelled, physically plausible, flag rates in the
bands the brief asks for, no real identifiers, inside the size and time budget. Nothing here is observation."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "components/01_data_sources"
NOTE = "SYNTHETIC DATA generated for software testing. NOT real observations."


def load_module():
    spec = importlib.util.spec_from_file_location("realistic_data_t", GEN / "realistic_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha_tree(root: Path):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    base = tmp_path_factory.mktemp("realistic")
    t0 = time.time()
    a = load_module()
    a.run(base / "a", base / "keyA.csv")
    seconds = time.time() - t0
    b = load_module()
    b.run(base / "b", base / "keyB.csv")
    return {"a": base / "a", "b": base / "b", "keyA": base / "keyA.csv", "keyB": base / "keyB.csv", "seconds": seconds}


def files(root, pattern):
    return sorted(root.glob(pattern))


# ---------------------------------------------------------------- determinism ----
def test_same_seed_same_sha256_for_every_file_and_the_answer_key(gen):
    assert sha_tree(gen["a"]) == sha_tree(gen["b"])
    assert gen["keyA"].read_bytes() == gen["keyB"].read_bytes()


def test_the_committed_set_equals_a_fresh_run_and_its_manifest_lists_every_file(gen):
    committed = ROOT / "data/realistic"
    lines = (committed / "MANIFEST.sha256").read_text().strip().splitlines()
    listed = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in lines}
    fresh = sha_tree(gen["a"])
    data_files = {k: v for k, v in fresh.items() if k not in ("MANIFEST.sha256", "README.txt")}
    assert listed == data_files
    for name, digest in listed.items():
        assert hashlib.sha256((committed / name).read_bytes()).hexdigest() == digest, name


def test_the_small_profile_is_unchanged():
    listed = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in (ROOT / "data/raw/MANIFEST.sha256").read_text().strip().splitlines()}
    for name, digest in listed.items():
        assert hashlib.sha256((ROOT / "data/raw" / name).read_bytes()).hexdigest() == digest, name


# ----------------------------------------------------------------- labelling ----
def test_every_file_says_it_is_synthetic(gen):
    n = 0
    for p in gen["a"].rglob("*.nc"):
        ds = xr.open_dataset(p, decode_times=False)
        assert ds.attrs.get("comment") == NOTE and ds.attrs.get("is_synthetic") == "true", p
        n += 1
    for p in gen["a"].rglob("*.csv"):
        head = p.read_text().splitlines()[:6]
        assert head[0] == f"# {NOTE}" and "# synthetic: true" in head, p
        n += 1
    assert n == 16
    assert NOTE in (gen["a"] / "README.txt").read_text()


def test_no_real_identifiers_only_fictional_ones(gen):
    for p in gen["a"].rglob("*.nc"):
        if "argo" in p.name:
            ids = {x.decode().strip() for x in xr.open_dataset(p, decode_times=False).PLATFORM_NUMBER.values}
            assert len(ids) > 3 and all(re.fullmatch(r"SYN-[ABE]\d{3}", i) for i in ids), ids
            assert not any(re.fullmatch(r"\d{7}", i) for i in ids)                     # a real WMO number has seven digits
    for p in files(gen["a"], "buoy_met_wave/*.csv"):
        assert re.search(r"^# platform: SYN-BUOY-[A-Z]{2}\d{2}$", p.read_text(), re.M), p
    for p in files(gen["a"], "ship_ctd/*.csv"):
        t = p.read_text()
        assert re.search(r"^# cruise: SYN-CRUISE-\d{4}-\d{2}$", t, re.M) and "RV Synthetic Explorer (fictional)" in t, p
        assert pd.read_csv(p, comment="#").cast_id.str.fullmatch(r"SYN-C\d{3}").all(), p
    blob = b"".join(p.read_bytes() for p in gen["a"].rglob("*.csv"))
    assert not re.search(rb"\b(NOAA|ECMWF|INCOIS|NIO|RAMA|ARGO\s?\d|Sagar|Kaustubh|Sindhu)\b", blob)


def test_the_labels_of_the_faults_are_never_inside_the_data_files(gen):
    for p in gen["a"].rglob("*.csv"):
        t = p.read_text().lower()
        assert not any(w in t for w in ("injected", "spike", "stuck", "answer_key", "error_type")), p
    for p in gen["a"].rglob("*.nc"):
        a = xr.open_dataset(p, decode_times=False).attrs
        assert not any(w in " ".join(map(str, a.values())).lower() for w in ("injected", "spike", "stuck", "fault")), p


# ------------------------------------------------------------------ budgets ----
def test_size_and_runtime_budget(gen):
    total = sum(p.stat().st_size for p in gen["a"].rglob("*") if p.is_file())
    assert total < 40 * 1024 * 1024, total
    assert gen["seconds"] < 300, gen["seconds"]


def test_counts_match_the_brief(gen):
    ids = set()
    n_prof = 0
    for p in files(gen["a"], "argo_profiles/*.nc"):
        d = xr.open_dataset(p, decode_times=False)
        ids |= {x.decode().strip() for x in d.PLATFORM_NUMBER.values}
        n_prof += d.sizes["N_PROF"]
        assert 70 <= d.sizes["N_LEVELS"] <= 120
        assert float(d.PRES.where(d.PRES < 90000).max()) <= 2000
    assert len(ids) == 40 and n_prof > 2000
    casts = sum(pd.read_csv(p, comment="#").cast_id.nunique() for p in files(gen["a"], "ship_ctd/*.csv"))
    assert len(files(gen["a"], "ship_ctd/*.csv")) == 6 and 60 <= casts <= 250
    assert len(files(gen["a"], "buoy_met_wave/*.csv")) == 5
    sst = xr.open_dataset(files(gen["a"], "sst_grid/*.nc")[0])
    assert sst.sizes["time"] >= 731 and abs(float(sst.lat[1] - sst.lat[0]) - 0.25) < 1e-6
    bat = xr.open_dataset(files(gen["a"], "bathymetry/*.nc")[0])
    assert abs(float(bat.lat[1] - bat.lat[0]) - 0.1) < 1e-6 and float(bat.lat.min()) <= -5 and float(bat.lat.max()) >= 25 and float(bat.lon.max()) >= 100


# ------------------------------------------------------------------ provider flags ----
def test_argo_provider_flag_rates_are_in_the_bands(gen):
    for p in files(gen["a"], "argo_profiles/*.nc"):
        d = xr.open_dataset(p, decode_times=False)
        q = np.concatenate([d.TEMP_QC.values.ravel(), d.PSAL_QC.values.ravel()])
        share = {k: float((q == k.encode()).mean()) for k in "012349"}
        assert 0.90 <= share["1"] <= 0.955, (p.name, share)
        assert 0.02 <= share["2"] <= 0.05, (p.name, share)
        assert 0.0 < share["3"] < 0.02 and 0.0 < share["4"] < 0.01, (p.name, share)
        assert 0.0 < share["0"] < 0.02 and 0.0 < share["9"] < 0.03, (p.name, share)


# ------------------------------------------------------------------- physics ----
def test_argo_profiles_are_stably_stratified_except_where_a_fault_was_injected(gen):
    import gsw
    key = list(csv.DictReader(open(gen["keyA"])))
    injected = {(r["file"], int(r["record_index"])) for r in key if r["error_type"] in ("density_inversion", "spike", "out_of_range") and r["record_kind"] == "profile_index"}
    bad = 0
    for p in files(gen["a"], "argo_profiles/*.nc"):
        d = xr.open_dataset(p, decode_times=False)
        for i in range(0, d.sizes["N_PROF"], 7):
            if (p.name, i) in injected:
                continue
            m = (d.TEMP.values[i] < 90000) & (d.PSAL.values[i] < 90000)
            if m.sum() < 60 or abs(d.LATITUDE.values[i]) > 30:
                continue
            pr, t, s = d.PRES.values[i][m], d.TEMP.values[i][m], d.PSAL.values[i][m]
            sa = gsw.SA_from_SP(s, pr, d.LONGITUDE.values[i], d.LATITUDE.values[i])
            sig = gsw.sigma0(sa, gsw.CT_from_t(sa, t, pr))
            bad += int((np.diff(sig) < -0.002).any())
    assert bad <= 3, bad                                    # a handful of noise-level inversions are allowed; a clean physics model has none


def test_water_masses_and_the_deep_ocean(gen):
    rows = []
    for p in files(gen["a"], "argo_profiles/*.nc"):
        d = xr.open_dataset(p, decode_times=False)
        for i in range(d.sizes["N_PROF"]):
            ok = d.PSAL.values[i] < 90000
            if ok.sum() < 90:
                continue
            surf = float(np.nanmedian(d.PSAL.values[i][:3]))
            rows.append((p.name, float(d.LATITUDE.values[i]), float(d.LONGITUDE.values[i]), surf, d.PSAL.values[i][ok], d.PRES.values[i][ok], d.TEMP.values[i][ok]))
    arab = np.mean([r[3] for r in rows if "arabian" in r[0] and r[1] > 12])
    bay = np.mean([r[3] for r in rows if "bay" in r[0] and r[1] > 12])
    assert arab > 35.2 and bay < 33.8 and arab - bay > 2.0                        # saline Arabian Sea against a freshened Bay of Bengal
    # the Persian Gulf tongue: salinity near 250 dbar higher in the north-west than in the south-east of the Arabian Sea
    nw = [float(np.interp(250, r[5], r[4])) for r in rows if "arabian" in r[0] and r[1] > 18 and r[2] < 66]
    se = [float(np.interp(250, r[5], r[4])) for r in rows if "arabian" in r[0] and r[1] < 10 and r[2] > 66]
    assert nw and se and np.mean(nw) - np.mean(se) > 0.25, (np.mean(nw), np.mean(se))
    deep = [float(np.interp(1900, r[5], r[6])) for r in rows]
    assert 1.5 < np.mean(deep) < 3.2 and max(deep) < 4.5
    thermo = [float(np.interp(50, r[5], r[6]) - np.interp(300, r[5], r[6])) for r in rows if r[1] < 15]
    assert np.mean(thermo) > 5.0                                                  # a real thermocline


def test_ship_ctd_deep_water_is_cold_and_casts_go_deep(gen):
    depths, deepT = [], []
    for p in files(gen["a"], "ship_ctd/*.csv"):
        df = pd.read_csv(p, comment="#")
        depths += df.groupby("cast_id").depth_m.max().tolist()
        deepT += df[(df.depth_m > 3000) & (df.temp_C.between(-5, 40))].temp_C.tolist()
    assert min(depths) >= 1000 and max(depths) <= 4000 and max(depths) >= 3500
    assert 1.4 < np.mean(deepT) < 2.0, np.mean(deepT)


def test_sst_monsoon_cycle_warm_pool_upwelling_and_cloud_gaps(gen):
    d = xr.open_dataset(files(gen["a"], "sst_grid/*.nc")[0])
    sst = d.analysed_sst - 273.15
    assert float(sst.max()) > 30.0 and 0 < float(sst.min()) < 26.0                # a spring warm pool above 30 degC
    spring = sst.sel(time=slice("2024-04-10", "2024-05-20")).max("time")
    assert float((spring > 30).sum()) > 100
    july = sst.sel(time="2024-07-15")
    up = float(july.sel(lat=slice(6, 10), lon=slice(52, 56)).mean())
    open_water = float(july.sel(lat=slice(6, 10), lon=slice(66, 72)).mean())
    oman = float(july.sel(lat=slice(18, 21), lon=slice(57, 61)).mean())
    assert open_water - up > 1.5 and open_water - oman > 1.5, (up, oman, open_water)   # Somali and Omani upwelling
    box = dict(lat=slice(8, 16), lon=slice(66, 76))
    may = float(sst.sel(time=slice("2024-04-25", "2024-05-15")).sel(**box).mean())
    jul = float(sst.sel(time=slice("2024-07-10", "2024-07-30")).sel(**box).mean())
    assert may - jul > 1.2, (may, jul)                                                          # the spring warm pool, before the monsoon cools the sea
    jan = float(sst.sel(time="2024-01-15").sel(lat=slice(21, 24), lon=slice(62, 70)).mean())
    assert open_water - jan > 1.5 or jan < 25.5                                          # the north cools in winter
    mask = np.isfinite(sst.isel(time=slice(0, 731, 15)).values).mean(axis=0) > 0.3        # cells that are sea
    gap_win = np.isnan(sst.sel(time=slice("2024-01-05", "2024-01-25")).values)[:, mask].mean()
    gap_mon = np.isnan(sst.sel(time=slice("2024-07-05", "2024-07-25")).values)[:, mask].mean()
    assert gap_mon > gap_win + 0.05 and 0.03 < gap_win < 0.5, (gap_win, gap_mon)         # more cloud in the monsoon
    raw = xr.open_dataset(files(gen["a"], "sst_grid/*.nc")[0], mask_and_scale=False)
    assert raw.analysed_sst.dtype == np.int16 and raw.analysed_sst.attrs.get("scale_factor") == 0.01       # GHRSST-style packing


def read_buoy(path):
    return pd.read_csv(path, comment="#", parse_dates=["time_utc"])


def test_buoys_monsoon_reversal_cyclones_outages_and_the_silent_buoy(gen):
    ps = files(gen["a"], "buoy_met_wave/*.csv")
    silent = 0
    for p in ps:
        df = read_buoy(p)
        ok = df[(df.wspd_ms < 60) & (df.wspd_ms >= 0)]
        sw = ok[ok.time_utc.dt.month.isin([7, 8])].wdir_deg
        ne = ok[ok.time_utc.dt.month.isin([12, 1])].wdir_deg
        assert ((sw > 180) & (sw < 270)).mean() > 0.6 and ((ne > 20) & (ne < 100)).mean() > 0.5, (p.name, sw.mean(), ne.mean())
        # at most one cyclone per basin per year: peaks of more than 25 m/s separated by weeks
        sustained = ok.wspd_ms.rolling(5, center=True).min() > 15                                  # a cyclone lasts many hours; an injected single-hour spike does not
        peaks = ok[sustained].time_utc
        starts = peaks[peaks.diff().isna() | (peaks.diff() > pd.Timedelta(days=5))]
        assert starts.dt.year.value_counts().max() <= 1 if len(starts) else True, (p.name, list(starts))     # at most one a year
        gaps = df.time_utc.diff()
        assert (gaps > pd.Timedelta(hours=3)).sum() >= 5                   # outages of hours to days
        silent += int((gaps > pd.Timedelta(days=20)).any())
        assert df[df.wspd_ms >= 0].slp_hPa.between(940, 1040).mean() > 0.99
        g = ok.groupby(ok.time_utc.dt.hour).airtemp_C.mean()
        assert g.max() - g.min() > 0.5                                       # a daily cycle
        c = ok[ok.hs_m > 0]
        assert np.corrcoef(c.wspd_ms, c.hs_m)[0, 1] > 0.5                    # waves follow the wind
    assert silent == 1                                                       # exactly one buoy is silent for three weeks
    cyc = sum(1 for p in ps if read_buoy(p).query("wspd_ms < 60").wspd_ms.max() > 30)
    assert 2 <= cyc <= 4


def test_bathymetry_has_shelf_slope_and_ridges(gen):
    e = xr.open_dataset(files(gen["a"], "bathymetry/*.nc")[0]).elevation
    assert float(e.min()) < -3500 and float(e.max()) > 100 and float((e < 0).mean()) > 0.6
    assert not np.isnan(e.values.astype(float)).any()                        # covers the whole region
    crest = float(e.sel(lat=6.0, lon=slice(72.8, 73.2)).mean())                                  # the Chagos-Laccadive-style ridge near 73E
    flanks = min(float(e.sel(lat=6.0, lon=slice(70.5, 71.5)).mean()), float(e.sel(lat=6.0, lon=slice(74.8, 75.8)).mean()))
    assert crest > flanks + 1500, (crest, flanks)


def test_natural_noise_is_small_and_heavy_tailed(gen):
    p = files(gen["a"], "ship_ctd/*.csv")[0]
    df = pd.read_csv(p, comment="#")
    d = df[df.depth_m.between(1500, 3000)].groupby("cast_id").temp_C.diff().dropna()
    assert 0.0005 < d.std() < 0.05


# ------------------------------------------------------------------- the key ----
def test_the_answer_key_lives_outside_the_data_and_has_every_fault_family(gen):
    rows = list(csv.DictReader(open(gen["keyA"])))
    kinds = {r["error_type"] for r in rows}
    need = {"spike", "out_of_range", "missing", "density_inversion", "position_on_land", "position_invalid", "timestamp_duplicate", "timestamp_jump_forward",
            "timestamp_non_monotonic", "stuck_value", "salinity_drift", "pressure_offset", "sensor_drift"}
    assert need <= kinds, need - kinds
    assert {r["expect"] for r in rows} == {"detect", "not_covered"}
    assert (ROOT / "data/blind_errors/realistic/answer_key.csv").read_bytes() == gen["keyA"].read_bytes()
    drift_floats = [r for r in rows if r["error_type"] == "salinity_drift"]
    assert len(drift_floats) >= 2
    assert not list(gen["a"].rglob("answer_key*"))


def test_qc_config_is_untouched():
    h = hashlib.sha256((ROOT / "components/03_qc_framework/scats_qc/qc_config.yaml").read_bytes()).hexdigest()
    assert h == "225bc3afe0a0ff0beddd693b22c7b23c2d4994c3bc1f3ebf2ba5268a1c9130f3"
