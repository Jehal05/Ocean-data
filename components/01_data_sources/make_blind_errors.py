#!/usr/bin/env python3
"""Build a SECOND error set ("blind" set) from the same clean files, to test the QC thresholds on sizes they were not built for.

Differences from inject_errors.py (the original set):
  * different subsets: Argo profiles 20-39 (floats 2 and 3), ship CTD casts SYN-C005..C007, buoy days 15-21
  * magnitudes that straddle the frozen QC thresholds: just below (negative controls, expected NOT flagged), just above, well above
  * its own answer key in data/blind_errors/answer_key.csv with two extra columns:
      magnitude_band   below_threshold | just_above | well_above | just_above_global | just_inside_global | n/a | swapped_pair
      expect           detect | no_flag        (no_flag = negative control: must not receive flag 3 or 4)
This script never runs the QC code. It only checks that each injection really is in the file it wrote.
Honest limits: the set is synthetic and written by the same author as the QC tests; it was written AFTER the QC config was frozen
(see reports/qc_config_frozen.sha256) but the author knew the thresholds. Negative controls exist for the buoy series and two
profile cases only: any perturbation big enough to matter in a profile also changes density and is caught by density_inversion.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from global_land_mask import globe

FILL_ARGO, FILL_CSV = 99999.0, -999
KEY: list[dict] = []
CHECKS: list[tuple[str, bool]] = []
SEED = 20261124                 # only used to pick the (unused-by-QC) decoration of notes; positions are fixed and listed below


def log(file, rec, var, lvl, etype, orig, inj, test, band, expect="detect", notes="", cast_id="", depth_m=""):
    KEY.append({"error_id": f"B{len(KEY) + 1:02d}", "file": file,
                "record_kind": "profile_index" if file.endswith(".nc") else "csv_data_row", "record_index": int(rec),
                "level_index": "" if lvl in ("-", "", None) else int(lvl), "cast_id": cast_id, "depth_m": depth_m,
                "variable": var, "error_type": etype, "original_value": orig, "injected_value": inj,
                "expected_primary_test": test, "notes": notes, "magnitude_band": band, "expect": expect})


def check(label, ok):
    CHECKS.append((label, bool(ok)))


# ----------------------------------------------------------------------------- Argo
def argo(raw: Path, out: Path):
    name = "argo_blind_errors.nc"
    src = raw / "argo_profiles/argo_synthetic_arabian_sea_2025Q1.nc"
    ds = xr.open_dataset(src, mask_and_scale=False, decode_times=False).isel(N_PROF=slice(20, 40)).load()
    T, S = ds["TEMP"].values.copy(), ds["PSAL"].values.copy()
    lat, lon, juld = ds["LATITUDE"].values.copy(), ds["LONGITUDE"].values.copy(), ds["JULD"].values.copy()
    f = name

    def bump(var, arr, rec, lvl, delta, etype, test, band, expect="detect", note=""):
        new = round(float(arr[rec, lvl] + delta), 4)
        log(f, rec, var, lvl, etype, round(float(arr[rec, lvl]), 4), new, test, band, expect, note)
        arr[rec, lvl] = new

    def setv(var, arr, rec, lvl, value, etype, test, band, expect="detect", note=""):
        log(f, rec, var, lvl, etype, round(float(arr[rec, lvl]), 4), value, test, band, expect, note)
        arr[rec, lvl] = value

    # levels 0-58 are above 500 dbar, 59+ are at or below. Thresholds (frozen config 0.2.0): T 6.0/2.0 degC, S 0.9/0.3
    bump("TEMP", T, 0, 8, 7.5, "spike", "spike", "just_above", note="1.25 x the shallow temperature threshold (6.0)")
    bump("TEMP", T, 1, 70, 2.5, "spike", "spike", "just_above", note="1.25 x the deep temperature threshold (2.0)")
    bump("PSAL", S, 2, 12, 1.2, "spike", "spike", "just_above", note="1.33 x the shallow salinity threshold (0.9)")
    bump("PSAL", S, 3, 66, 0.4, "spike", "spike", "just_above", note="1.33 x the deep salinity threshold (0.3)")
    bump("TEMP", T, 5, 30, 18.0, "spike", "spike", "well_above", note="3 x the shallow threshold")
    setv("PSAL", S, 6, 40, 42.3, "out_of_range", "global_range", "just_above_global", note="valid_max is 42")
    setv("TEMP", T, 8, 50, -2.8, "out_of_range", "global_range", "just_above_global", note="valid_min is -2.5")
    setv("PSAL", S, 9, 25, 41.6, "out_of_range", "regional_range", "just_inside_global", note="inside 2-42, above regional 41 (soft flag expected)")
    setv("TEMP", T, 10, 33, FILL_ARGO, "missing", "missing_value", "n/a", note="Argo fill value 99999")
    setv("PSAL", S, 11, 45, FILL_ARGO, "missing", "missing_value", "n/a", note="Argo fill value 99999")
    # positions
    log(f, 12, "LATITUDE/LONGITUDE", "-", "position_on_land", f"{lat[12]:.3f},{lon[12]:.3f}", "25.200,68.500", "position_on_land", "n/a", notes="Sindh, Pakistan")
    lat[12], lon[12] = 25.2, 68.5
    log(f, 13, "LATITUDE", "-", "position_invalid", lat[13], 91.5, "position_range", "just_above_global", notes="latitude beyond 90 by 1.5")
    lat[13] = 91.5
    # time (subset indices 0-9 are float 2, 10-19 float 3)
    log(f, 4, "JULD", "-", "timestamp_jump_forward", juld[4], juld[4] + 40.0, "timestamp_monotonic", "n/a", notes="+40 days, later than its successor")
    juld[4] += 40.0
    log(f, 7, "JULD", "-", "timestamp_duplicate", juld[7], juld[6], "timestamp_monotonic", "n/a", notes="same time as the previous profile")
    juld[7] = juld[6]
    log(f, 14, "JULD", "-", "timestamp_non_monotonic", juld[14], juld[14] - 30.0, "timestamp_monotonic", "n/a", notes="-30 days, earlier than two predecessors")
    juld[14] -= 30.0
    a, b = juld[17], juld[18]
    log(f, 17, "JULD", "-", "timestamp_non_monotonic", a, b, "timestamp_monotonic", "swapped_pair", notes="records 17 and 18 swapped: ambiguous by design")
    log(f, 18, "JULD", "-", "timestamp_non_monotonic", b, a, "timestamp_monotonic", "swapped_pair", notes="records 17 and 18 swapped: ambiguous by design")
    juld[17], juld[18] = b, a
    # density inversions (deep, so the spike threshold of 2.0 degC is NOT reached)
    bump("TEMP", T, 15, 75, 0.4, "density_inversion", "density_inversion", "just_above", note="deep +0.4 degC, about twice the 0.03 kg m-3 tolerance")
    bump("TEMP", T, 16, 68, 1.0, "density_inversion", "density_inversion", "well_above", note="deep +1.0 degC")
    # negative controls
    bump("TEMP", T, 19, 72, 0.1, "density_inversion", "density_inversion", "below_threshold", "no_flag", "deep +0.1 degC, below the density tolerance")
    setv("TEMP", T, 16, 0, 35.5, "out_of_range", "regional_range", "below_threshold", "no_flag", "inside the regional range (36); surface level is never spike-tested and the pair below stays stable")

    ds["TEMP"].values[:], ds["PSAL"].values[:] = T, S
    ds["LATITUDE"].values[:], ds["LONGITUDE"].values[:], ds["JULD"].values[:] = lat, lon, juld
    ds.attrs["comment"] = "SYNTHETIC BLIND ERROR SET for QC testing. See answer_key.csv. NOT real observations."
    ds.to_netcdf(out / name)
    r = xr.open_dataset(out / name, mask_and_scale=False, decode_times=False)
    check("argo land position", bool(globe.is_land(r["LATITUDE"].values[12], r["LONGITUDE"].values[12])))
    check("argo invalid latitude", abs(r["LATITUDE"].values[13]) > 90)
    check("argo dup time", r["JULD"].values[7] == r["JULD"].values[6])
    check("argo missing", r["TEMP"].values[10, 33] == FILL_ARGO and r["PSAL"].values[11, 45] == FILL_ARGO)
    check("argo S out of range", r["PSAL"].values[6, 40] == 42.3)
    return out / name


# ----------------------------------------------------------------------- CSV helpers
def csv_subset(src: Path, out: Path, name: str):
    header = [ln for ln in open(src) if ln.startswith("#")]
    return header, pd.read_csv(src, comment="#"), out / name


def write_csv(header, df, path):
    with open(path, "w", newline="") as fh:
        fh.writelines(header)
        fh.write("# comment: SYNTHETIC BLIND ERROR SET for QC testing. See answer_key.csv. NOT real observations.\n")
        df.to_csv(fh, index=False, lineterminator="\n")


def ctd(raw: Path, out: Path):
    name = "ctd_blind_errors.csv"
    header, df, path = csv_subset(raw / "ship_ctd/ctd_casts_synthetic_arabian_sea_2025.csv", out, name)
    df = df[df.cast_id.isin(["SYN-C005", "SYN-C006", "SYN-C007"])].reset_index(drop=True)

    def row(cast, level):
        return int(df.index[(df.cast_id == cast) & (np.isclose(df.depth_m, level * 2.0))][0])

    def put(cast, level, col, new, etype, test, band, expect="detect", note="", delta=False):
        i = row(cast, level)
        old = float(df.at[i, col])
        val = round(old + new, 4) if delta else new
        log(name, i, col, level, etype, old, "(empty)" if val is None else val, test, band, expect, note, cast_id=cast, depth_m=df.at[i, "depth_m"])
        df.at[i, col] = np.nan if val is None else val

    put("SYN-C005", 60, "temp_C", 7.5, "spike", "spike", "just_above", note="1.25 x shallow threshold", delta=True)
    put("SYN-C005", 100, "sal_PSU", -1.2, "spike", "spike", "just_above", note="1.33 x shallow salinity threshold", delta=True)
    put("SYN-C006", 249, "temp_C", 2.5, "spike", "spike", "just_above", note="depth 498 m, about 503 dbar: deep threshold 2.0", delta=True)
    put("SYN-C006", 120, "temp_C", None, "missing", "missing_value", "n/a", note="empty cell")
    put("SYN-C006", 80, "sal_PSU", 42.4, "out_of_range", "global_range", "just_above_global", note="valid_max is 42")
    put("SYN-C007", 200, "temp_C", 0.5, "density_inversion", "density_inversion", "just_above", note="deep +0.5 degC", delta=True)
    put("SYN-C007", 220, "temp_C", 0.1, "density_inversion", "density_inversion", "below_threshold", "no_flag", "deep +0.1 degC below the tolerance", delta=True)
    write_csv(header, df, path)
    return path


def buoy(raw: Path, out: Path):
    name = "buoy_blind_errors.csv"
    header, df, path = csv_subset(raw / "buoy_met_wave/buoy_met_wave_synthetic_2025Q1.csv", out, name)
    df = df.iloc[360:528].reset_index(drop=True)                 # days 15-21
    # (record, column, change, band, expect, spike threshold note). Series thresholds (frozen config): wind 10, air T 4, RH 25,
    # pressure 5, Hs 2, Tp 8, SST 1.5, current 1.0 m/s = 100 cm/s
    spikes = [(20, "wspd_ms", 12.5, "just_above", "detect", "1.25 x 10"), (30, "wspd_ms", 7.0, "below_threshold", "no_flag", "0.7 x 10"),
              (40, "airtemp_C", 5.0, "just_above", "detect", "1.25 x 4"), (50, "airtemp_C", 2.8, "below_threshold", "no_flag", "0.7 x 4"),
              (60, "rh_pct", -32.0, "just_above", "detect", "1.28 x 25"), (70, "rh_pct", -17.5, "below_threshold", "no_flag", "0.7 x 25"),
              (80, "slp_hPa", 6.3, "just_above", "detect", "1.26 x 5"), (90, "slp_hPa", 3.5, "below_threshold", "no_flag", "0.7 x 5"),
              (100, "hs_m", 2.5, "just_above", "detect", "1.25 x 2"), (110, "hs_m", 1.4, "below_threshold", "no_flag", "0.7 x 2"),
              (120, "sst_C", 1.9, "just_above", "detect", "1.27 x 1.5"), (130, "sst_C", 1.0, "below_threshold", "no_flag", "0.67 x 1.5"),
              (140, "tp_s", 10.0, "just_above", "detect", "1.25 x 8"), (150, "tp_s", 5.5, "below_threshold", "no_flag", "0.69 x 8"),
              (35, "cur_speed_cm_s", 125.0, "just_above", "detect", "1.25 x 100 cm/s"), (45, "cur_speed_cm_s", 70.0, "below_threshold", "no_flag", "0.7 x 100 cm/s")]
    for rec, col, delta, band, expect, note in spikes:
        old = float(df.at[rec, col]); new = round(old + delta, 4)
        log(name, rec, col, "", "spike", old, new, "spike", band, expect, note)
        df.at[rec, col] = new
    for rec, col, val, test, band in [(55, "wspd_ms", 75.5, "global_range", "just_above_global"), (65, "hs_m", 30.5, "global_range", "just_above_global"),
                                       (75, "slp_hPa", 868.5, "global_range", "just_above_global"), (85, "rh_pct", 100.6, "global_range", "just_above_global"),
                                       (95, "wspd_ms", 41.0, "regional_range", "just_inside_global")]:
        old = float(df.at[rec, col]); log(name, rec, col, "", "out_of_range", old, val, test, band, "detect", "")
        df.at[rec, col] = val
    for rec, col in [(105, "airtemp_C"), (115, "hs_m")]:
        log(name, rec, col, "", "missing", float(df.at[rec, col]), "(empty)", "missing_value", "n/a")
        df.at[rec, col] = np.nan
    log(name, 125, "lat/lon", "", "position_on_land", f"{df.at[125, 'lat']},{df.at[125, 'lon']}", "24.000,68.000", "position_on_land", "n/a", notes="Sindh, Pakistan")
    df.at[125, "lat"], df.at[125, "lon"] = 24.0, 68.0
    log(name, 135, "lat/lon", "", "position_invalid", df.at[135, "lat"], 90.5, "position_range", "just_above_global", notes="latitude beyond 90 by 0.5")
    df.at[135, "lat"] = 90.5
    t = pd.to_datetime(df["time_utc"], utc=True)
    fmt = lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    log(name, 145, "time_utc", "", "timestamp_duplicate", df.at[145, "time_utc"], df.at[144, "time_utc"], "timestamp_monotonic", "n/a")
    df.at[145, "time_utc"] = df.at[144, "time_utc"]
    log(name, 155, "time_utc", "", "timestamp_jump_forward", df.at[155, "time_utc"], fmt(t[155] + pd.Timedelta(hours=400)), "timestamp_monotonic", "n/a", notes="+400 h")
    df.at[155, "time_utc"] = fmt(t[155] + pd.Timedelta(hours=400))
    log(name, 160, "time_utc", "", "timestamp_non_monotonic", df.at[160, "time_utc"], fmt(t[160] - pd.Timedelta(hours=300)), "timestamp_monotonic", "n/a", notes="-300 h")
    df.at[160, "time_utc"] = fmt(t[160] - pd.Timedelta(hours=300))
    a, b = df.at[163, "time_utc"], df.at[164, "time_utc"]
    log(name, 163, "time_utc", "", "timestamp_non_monotonic", a, b, "timestamp_monotonic", "swapped_pair", notes="163 and 164 swapped: ambiguous by design")
    log(name, 164, "time_utc", "", "timestamp_non_monotonic", b, a, "timestamp_monotonic", "swapped_pair", notes="163 and 164 swapped: ambiguous by design")
    df.at[163, "time_utc"], df.at[164, "time_utc"] = b, a
    write_csv(header, df, path)
    r = pd.read_csv(path, comment="#")
    check("buoy land", bool(globe.is_land(r.at[125, "lat"], r.at[125, "lon"])))
    check("buoy dup time", r.at[145, "time_utc"] == r.at[144, "time_utc"])
    check("buoy missing", r["airtemp_C"].isna()[105] and r["hs_m"].isna()[115])
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="data/raw", type=Path)
    ap.add_argument("--out", default="data/blind_errors", type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for fn in (argo, ctd, buoy):
        print("wrote", fn(a.raw, a.out))
    key = pd.DataFrame(KEY)
    key.to_csv(a.out / "answer_key.csv", index=False)
    print(f"\nanswer_key.csv: {len(key)} rows ({(key.expect == 'detect').sum()} to detect, {(key.expect == 'no_flag').sum()} negative controls)")
    print(key.groupby(["error_type", "expect"]).size().to_string())
    bad = [label for label, ok in CHECKS if not ok]
    print(f"\nself-checks: {len(CHECKS) - len(bad)}/{len(CHECKS)} passed")
    for label in bad:
        print("  FAILED:", label)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
