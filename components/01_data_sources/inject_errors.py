#!/usr/bin/env python3
"""Build the error-injected test set and its answer key (Phase 1, task 5).

Reads the clean files in data/raw, writes SMALL corrupted copies to
data/test_errors, and records every injected error in answer_key.csv so that
Phase 2 can measure whether QC catches all of them.

Files written (all three are subsets of the clean raw files):
  argo_profiles_errors.nc   first 20 Argo-style profiles    (NetCDF)
  ctd_casts_errors.csv      first 3 ship CTD casts          (CSV, long form)
  buoy_met_wave_errors.csv  first 7 days of buoy records    (CSV, time series)
  answer_key.csv            one row per injected error

Answer key columns (ALL indices are 0-based):
  record_kind    profile_index  -> record_index is the profile number in the .nc file
                 csv_data_row   -> record_index is the data row in the CSV, counting
                                   from 0 after the "#" header lines and the column header
  record_index   see above
  level_index    level number within the profile or cast (blank when not applicable)
  cast_id, depth_m   ship CTD only

Every injection is checked after writing: the script re-reads the output and
proves the error is really there (e.g. the "land" position is on land, the
"inversion" is a real density inversion). It exits non-zero if any check fails.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gsw
import numpy as np
import pandas as pd
import xarray as xr
from global_land_mask import globe

FILL_ARGO = 99999.0
FILL_CSV = -999
KEY: list[dict] = []
CHECKS: list[tuple[str, bool]] = []


def log(file, record_index, variable, level_index, etype, original, injected, test, notes="",
        cast_id="", depth_m=""):
    """All indices are 0-based. See the module docstring for the column meanings."""
    KEY.append({"error_id": f"E{len(KEY) + 1:02d}", "file": file,
                "record_kind": "profile_index" if file.endswith(".nc") else "csv_data_row",
                "record_index": int(record_index),
                "level_index": "" if level_index in ("-", "", None) else int(level_index),
                "cast_id": cast_id, "depth_m": depth_m, "variable": variable,
                "error_type": etype, "original_value": original, "injected_value": injected,
                "expected_primary_test": test, "notes": notes})


def check(label, ok):
    CHECKS.append((label, bool(ok)))


def sigma0_profile(temp, sal, pres, lat, lon):
    sa = gsw.SA_from_SP(sal, pres, lon, lat)
    return gsw.sigma0(sa, gsw.CT_from_t(sa, temp, pres))


# ---------------------------------------------------------------- Argo ----
def argo_subset(raw: Path, out: Path):
    name = "argo_profiles_errors.nc"
    src = raw / "argo_profiles" / "argo_synthetic_arabian_sea_2025Q1.nc"
    ds = xr.open_dataset(src, mask_and_scale=False, decode_times=False).isel(N_PROF=slice(0, 20)).load()
    T, S = ds["TEMP"].values.copy(), ds["PSAL"].values.copy()
    lat, lon, juld = ds["LATITUDE"].values.copy(), ds["LONGITUDE"].values.copy(), ds["JULD"].values.copy()
    pres = ds["PRES"].values
    f = name

    # spikes
    log(f, 3, "TEMP", 40, "spike", T[3, 40], T[3, 40] + 12.0, "spike", "+12 degC jump at one level")
    T[3, 40] += 12.0
    log(f, 7, "PSAL", 55, "spike", S[7, 55], S[7, 55] + 3.0, "spike", "+3 psu jump at one level")
    S[7, 55] += 3.0
    # out of physical range
    log(f, 5, "TEMP", 10, "out_of_range", T[5, 10], -8.0, "global_range", "below freezing point of sea water")
    T[5, 10] = -8.0
    log(f, 9, "PSAL", 20, "out_of_range", S[9, 20], 52.0, "global_range", "salinity above any open-ocean value")
    S[9, 20] = 52.0
    # positions
    log(f, 11, "LATITUDE/LONGITUDE", "-", "position_on_land", f"{lat[11]:.3f},{lon[11]:.3f}", "23.300,72.000",
        "position_on_land", "inland Gujarat, India")
    lat[11], lon[11] = 23.3, 72.0
    log(f, 13, "LATITUDE", "-", "position_invalid", lat[13], 95.0, "position_range", "latitude beyond +/-90")
    lat[13] = 95.0
    # time
    log(f, 16, "JULD", "-", "timestamp_duplicate", juld[16], juld[15], "timestamp_monotonic",
        "same time as profile 15 of the same float")
    juld[16] = juld[15]
    log(f, 18, "JULD", "-", "timestamp_non_monotonic", juld[18], juld[15] - 3.0, "timestamp_monotonic",
        "earlier than profiles 15-17 of the same float")
    juld[18] = juld[15] - 3.0
    # missing values (Argo fill value 99999)
    log(f, 1, "TEMP", 30, "missing", T[1, 30], FILL_ARGO, "missing_value", "Argo fill value 99999")
    T[1, 30] = FILL_ARGO
    log(f, 2, "PSAL", 50, "missing", S[2, 50], FILL_ARGO, "missing_value", "Argo fill value 99999")
    S[2, 50] = FILL_ARGO
    # density inversion: much warmer water directly below a colder level
    clean_t = ds["TEMP"].values[19, 60]
    new_t = T[19, 59] + 6.0
    log(f, 19, "TEMP", 60, "density_inversion", clean_t, new_t, "density_inversion",
        "level 60 made 6 degC warmer than level 59; may also trip spike")
    T[19, 60] = new_t

    ds["TEMP"].values[:] = T
    ds["PSAL"].values[:] = S
    ds["LATITUDE"].values[:] = lat
    ds["LONGITUDE"].values[:] = lon
    ds["JULD"].values[:] = juld
    ds.attrs["comment"] = ("SYNTHETIC DATA with DELIBERATE ERRORS for QC testing. "
                           "See answer_key.csv. NOT real observations.")
    path = out / name
    ds.to_netcdf(path)

    # ---- verify by re-reading the file
    r = xr.open_dataset(path, mask_and_scale=False, decode_times=False)
    rT, rS, rlat, rlon, rj = (r[k].values for k in ("TEMP", "PSAL", "LATITUDE", "LONGITUDE", "JULD"))
    check("argo spike T", abs(rT[3, 40] - rT[3, 39]) > 8)
    check("argo spike S", abs(rS[7, 55] - rS[7, 54]) > 2)
    check("argo range T", rT[5, 10] < -2.5)
    check("argo range S", rS[9, 20] > 42)
    check("argo land position", bool(globe.is_land(rlat[11], rlon[11])))
    check("argo invalid latitude", abs(rlat[13]) > 90)
    check("argo duplicate JULD", rj[16] == rj[15])
    check("argo non-monotonic JULD", rj[18] < rj[17])
    check("argo missing T", rT[1, 30] == FILL_ARGO)
    check("argo missing S", rS[2, 50] == FILL_ARGO)
    p = r["PRES"].values[19]
    sig = sigma0_profile(rT[19], rS[19], p, rlat[19], rlon[19])
    check("argo density inversion", np.diff(sig)[59] < -0.5)
    clean = sigma0_profile(ds["TEMP"].values[0], ds["PSAL"].values[0], pres[0], rlat[0], rlon[0])
    check("argo untouched profile 0 still stable", (np.diff(clean) >= 0).all())
    return path


# ----------------------------------------------------------- ship CTD -----
def ctd_subset(raw: Path, out: Path):
    name = "ctd_casts_errors.csv"
    src = raw / "ship_ctd" / "ctd_casts_synthetic_arabian_sea_2025.csv"
    header = [ln for ln in open(src) if ln.startswith("#")]
    df = pd.read_csv(src, comment="#")
    df = df[df.cast_id.isin(["SYN-C001", "SYN-C002", "SYN-C003"])].reset_index(drop=True)
    f = name

    def row(cast, depth):
        return df.index[(df.cast_id == cast) & (df.depth_m == depth)][0]

    i = row("SYN-C001", 60.0)
    log(f, i, "temp_C", 30, "spike", df.at[i, "temp_C"], round(df.at[i, "temp_C"] + 10.0, 4),
        "spike", "+10 degC at 60 m", cast_id="SYN-C001", depth_m=60.0)
    df.at[i, "temp_C"] = round(df.at[i, "temp_C"] + 10.0, 4)
    i2 = row("SYN-C002", 100.0)
    log(f, i2, "sal_PSU", 50, "missing", df.at[i2, "sal_PSU"], FILL_CSV, "missing_value",
        "fill value -999", cast_id="SYN-C002", depth_m=100.0)
    df.at[i2, "sal_PSU"] = FILL_CSV
    i3 = row("SYN-C003", 150.0)
    below = row("SYN-C003", 152.0)
    new_t = round(df.at[i3, "temp_C"] + 5.0, 4)
    log(f, below, "temp_C", 76, "density_inversion", df.at[below, "temp_C"], new_t,
        "density_inversion", "152 m made 5 degC warmer than 150 m", cast_id="SYN-C003", depth_m=152.0)
    df.at[below, "temp_C"] = new_t
    i4 = row("SYN-C003", 300.0)
    log(f, i4, "sal_PSU", 150, "out_of_range", df.at[i4, "sal_PSU"], 75.0, "global_range",
        "salinity 75", cast_id="SYN-C003", depth_m=300.0)
    df.at[i4, "sal_PSU"] = 75.0

    path = out / name
    with open(path, "w", newline="") as fh:
        fh.writelines(header)
        df.to_csv(fh, index=False, lineterminator="\n")

    r = pd.read_csv(path, comment="#")
    c1 = r[r.cast_id == "SYN-C001"].set_index("depth_m")
    check("ctd spike", c1.at[60.0, "temp_C"] - c1.at[58.0, "temp_C"] > 8)
    check("ctd missing", (r[(r.cast_id == "SYN-C002") & (r.depth_m == 100.0)].sal_PSU == FILL_CSV).all())
    c3 = r[r.cast_id == "SYN-C003"]
    p = gsw.p_from_z(-c3.depth_m.values, c3.lat.iloc[0])
    sig = sigma0_profile(c3.temp_C.values, np.where(c3.sal_PSU.values > 60, 35.5, c3.sal_PSU.values),
                         p, c3.lat.iloc[0], c3.lon.iloc[0])
    k = int(np.where(c3.depth_m.values == 152.0)[0][0])
    check("ctd density inversion", sig[k] < sig[k - 1] - 0.3)
    check("ctd range", (r[(r.cast_id == "SYN-C003") & (r.depth_m == 300.0)].sal_PSU > 42).all())
    return path


# -------------------------------------------------------------- buoy ------
def buoy_subset(raw: Path, out: Path):
    name = "buoy_met_wave_errors.csv"
    src = raw / "buoy_met_wave" / "buoy_met_wave_synthetic_2025Q1.csv"
    header = [ln for ln in open(src) if ln.startswith("#")]
    df = pd.read_csv(src, comment="#", dtype={"time_utc": str}).iloc[:168].copy()
    f = name
  
    log(f, 40, "wspd_ms", "", "spike", df.at[40, "wspd_ms"], df.at[40, "wspd_ms"] + 35.0, "spike",
        "35 m/s jump for one hour")
    df.at[40, "wspd_ms"] = round(df.at[40, "wspd_ms"] + 35.0, 2)
    log(f, 90, "hs_m", "", "spike", df.at[90, "hs_m"], 9.5, "spike", "wave height jumps to 9.5 m for one hour")
    df.at[90, "hs_m"] = 9.5
    log(f, 60, "rh_pct", "", "out_of_range", df.at[60, "rh_pct"], 140.0, "global_range", "humidity above 100 %")
    df.at[60, "rh_pct"] = 140.0
    log(f, 61, "airtemp_C", "", "out_of_range", df.at[61, "airtemp_C"], 75.0, "global_range", "air temperature 75 degC")
    df.at[61, "airtemp_C"] = 75.0
    log(f, 20, "lat/lon", "", "position_on_land", f"{df.at[20, 'lat']},{df.at[20, 'lon']}", "19.0,74.0",
        "position_on_land", "inland Maharashtra, India; a moored buoy cannot be there")
    df.at[20, "lat"], df.at[20, "lon"] = 19.0, 74.0
    log(f, 100, "time_utc", "", "timestamp_duplicate", df.at[100, "time_utc"], df.at[99, "time_utc"],
        "timestamp_monotonic", "same time as the previous row")
    df.at[100, "time_utc"] = df.at[99, "time_utc"]
    earlier = (pd.Timestamp(df.at[125, "time_utc"]) - pd.Timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f, 130, "time_utc", "", "timestamp_non_monotonic", df.at[130, "time_utc"], earlier,
        "timestamp_monotonic", "two hours before an earlier row")
    df.at[130, "time_utc"] = earlier
    log(f, 70, "airtemp_C", "", "missing", df.at[70, "airtemp_C"], "(empty)", "missing_value", "empty cell")
    df.at[70, "airtemp_C"] = np.nan
    log(f, 71, "wdir_deg", "", "missing", df.at[71, "wdir_deg"], FILL_CSV, "missing_value", "fill value -999")
    df.at[71, "wdir_deg"] = FILL_CSV

    path = out / name
    with open(path, "w", newline="") as fh:
        fh.writelines(header)
        df.to_csv(fh, index=False, lineterminator="\n")

    r = pd.read_csv(path, comment="#", dtype={"time_utc": str})
    check("buoy spike wind", r.at[40, "wspd_ms"] - r.at[39, "wspd_ms"] > 25)
    check("buoy spike Hs", r.at[90, "hs_m"] - r.at[89, "hs_m"] > 6)
    check("buoy RH range", r.at[60, "rh_pct"] > 100)
    check("buoy air temp range", r.at[61, "airtemp_C"] > 60)
    check("buoy land position", bool(globe.is_land(r.at[20, "lat"], r.at[20, "lon"])))
    t = pd.to_datetime(r.time_utc)
    check("buoy duplicate time", t[100] == t[99])
    check("buoy non-monotonic time", t[130] < t[129])
    check("buoy empty cell", pd.isna(r.at[70, "airtemp_C"]))
    check("buoy fill value", r.at[71, "wdir_deg"] == FILL_CSV)
    return path


def verify_key(out: Path):
    """Prove every answer-key row points at the cell that was actually corrupted."""
    key = pd.DataFrame(KEY)
    argo = xr.open_dataset(out / "argo_profiles_errors.nc", mask_and_scale=False, decode_times=False)
    csvs = {n: pd.read_csv(out / n, comment="#", dtype={"time_utc": str})
            for n in ("ctd_casts_errors.csv", "buoy_met_wave_errors.csv")}
    num = lambda v: float(v)  # noqa: E731
    for _, k in key.iterrows():
        ri, var, label = int(k.record_index), k.variable, f"key {k.error_id}"
        inj = k.injected_value
        if k.record_kind == "profile_index":
            if var in ("TEMP", "PSAL"):
                ok = argo[var].values[ri, int(k.level_index)] == num(inj)
            elif var in ("LATITUDE", "JULD"):
                ok = argo[var].values[ri] == num(inj)
            else:  # LATITUDE/LONGITUDE pair
                ok = bool(globe.is_land(argo["LATITUDE"].values[ri], argo["LONGITUDE"].values[ri]))
        else:
            df = csvs[k.file]
            if k.cast_id:
                ok = (df.at[ri, "cast_id"] == k.cast_id and df.at[ri, "depth_m"] == num(k.depth_m)
                      and int(round(num(k.depth_m) / 2)) == int(k.level_index)
                      and df.at[ri, var] == num(inj))
            elif var == "lat/lon":
                ok = bool(globe.is_land(df.at[ri, "lat"], df.at[ri, "lon"]))
            elif var == "time_utc":
                ok = df.at[ri, var] == str(inj)
            elif str(inj) == "(empty)":
                ok = pd.isna(df.at[ri, var])
            else:
                ok = df.at[ri, var] == num(inj)
        check(f"answer key {k.error_id} points at the corrupted cell", ok)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="data/raw", type=Path)
    ap.add_argument("--out", default="data/test_errors", type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for fn in (argo_subset, ctd_subset, buoy_subset):
        print("wrote", fn(a.raw, a.out))
    verify_key(a.out)
    key = pd.DataFrame(KEY)
    key.to_csv(a.out / "answer_key.csv", index=False)
    print(f"\nanswer_key.csv: {len(key)} injected errors")
    print(key.groupby("error_type").size().to_string())
    bad = [label for label, ok in CHECKS if not ok]
    print(f"\nself-checks: {len(CHECKS) - len(bad)}/{len(CHECKS)} passed")
    for label in bad:
        print("  FAILED:", label)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
