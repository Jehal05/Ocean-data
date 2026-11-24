#!/usr/bin/env python3
"""Generate an OFFLINE, SYNTHETIC stand-in for the four Phase 1 dataset types.

Why this exists: real downloads (Argo, NOAA, ERA5, GEBCO) need internet access
and, for some, a login. This script produces files that look like those
products (names, units, fill values, packing) so Phases 2-5 can be built and
tested anywhere. Every file is labelled SYNTHETIC. None of it is observation.

Region : Arabian Sea / northern Indian Ocean, 8-24N, 60-75E
Window : 2025-01-01 to 2025-03-31 (90 days)

Outputs (under --out, default data/raw):
  argo_profiles/argo_synthetic_arabian_sea_2025Q1.nc    NetCDF  (Argo-style)
  ship_ctd/ctd_casts_synthetic_arabian_sea_2025.csv      CSV     (long form)
  sst_grid/sst_synthetic_0p25deg_2025Q1.nc               NetCDF  (GHRSST-style, packed)
  buoy_met_wave/buoy_met_wave_synthetic_2025Q1.csv       CSV     (hourly)
  bathymetry/bathymetry_synthetic_0p1deg.nc              NetCDF  (GEBCO-style)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import gsw
import numpy as np
import pandas as pd
import xarray as xr
from global_land_mask import globe
from scipy import ndimage

SEED = 20250101
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 8.0, 24.0, 60.0, 75.0
START = pd.Timestamp("2025-01-01")
NDAYS = 90
SYNTH_NOTE = "SYNTHETIC DATA generated for software testing. NOT real observations."


# ------------------------------------------------------------- physics ----
def sst_model(lat, day):
    """Winter/spring Arabian Sea surface temperature, degC (smooth, synthetic)."""
    return 28.4 - 0.19 * (np.asarray(lat) - 8.0) + 0.012 * day


def make_ts_profile(lat, lon, day, z, rng):
    """Temperature (in situ, degC) and practical salinity on depths z (m, positive down).

    Both fall monotonically with depth, so the clean profile is stably stratified.
    Small noise is added, then salinity is nudged until no density inversion remains.
    """
    z = np.asarray(z, dtype=float)
    sst = sst_model(lat, day)
    mld = 35.0 + 2.5 * (lat - 8.0)
    t_deep = 8.0 + 4.0 * np.exp(-z / 400.0)
    weight = 0.5 * (1.0 - np.tanh((z - mld - 40.0) / 55.0))
    temp = t_deep + (sst - t_deep) * weight
    s0 = 35.6 + 0.05 * (lat - 8.0)
    sal = 35.0 + (s0 - 35.0) * np.exp(-z / 350.0)
    temp = temp + rng.normal(0, 0.005, z.size)
    sal = sal + rng.normal(0, 0.001, z.size)
    return temp, remove_inversions(temp, sal, z, lat, lon)


def sigma0(temp, sal, z, lat, lon):
    p = gsw.p_from_z(-np.asarray(z), lat)
    sa = gsw.SA_from_SP(sal, p, lon, lat)
    ct = gsw.CT_from_t(sa, temp, p)
    return gsw.sigma0(sa, ct)


def remove_inversions(temp, sal, z, lat, lon):
    sal = sal.copy()
    for _ in range(400):
        inv = np.diff(sigma0(temp, sal, z, lat, lon)) < 0
        if not inv.any():
            return sal
        sal[1:][inv] += 0.0007
    raise RuntimeError("could not make clean profile statically stable")


def is_ocean(lat, lon):
    return (LAT_MIN <= lat <= LAT_MAX) and (LON_MIN <= lon <= LON_MAX) and not bool(
        globe.is_land(lat, lon))


# ------------------------------------------------------- 1. Argo-style ----
def make_argo(out: Path, rng):
    levels = np.concatenate([np.arange(5, 100, 5), np.arange(100, 500, 10),
                             np.arange(500, 1001, 25)]).astype("float32")  # 80 levels
    n_floats, n_cycles, cycle_days = 6, 10, 8
    starts = []
    while len(starts) < n_floats:
        lat, lon = rng.uniform(9, 22), rng.uniform(61, 74)
        if is_ocean(lat, lon) and not globe.is_land(lat + 1, lon) and not globe.is_land(lat - 1, lon):
            starts.append((lat, lon))

    rows = []
    for f, (lat, lon) in enumerate(starts):
        wmo = f"SYN{29000 + f}"   # 8 characters: the file stores PLATFORM_NUMBER as S8
        for c in range(n_cycles):
            day = 1.5 * f + c * cycle_days + rng.uniform(0, 0.4)
            rows.append((wmo, c + 1, day, lat, lon))
            for _ in range(60):  # random-walk drift that stays in the ocean
                nlat, nlon = lat + rng.normal(0, 0.25), lon + rng.normal(0, 0.25)
                if is_ocean(nlat, nlon):
                    lat, lon = nlat, nlon
                    break

    n_prof, n_lev = len(rows), levels.size
    temp = np.full((n_prof, n_lev), 99999.0, "float32")
    psal = np.full((n_prof, n_lev), 99999.0, "float32")
    for i, (_, _, day, lat, lon) in enumerate(rows):
        t, s = make_ts_profile(lat, lon, day, levels, rng)
        temp[i], psal[i] = t, s
    pres = np.tile(gsw.p_from_z(-levels.astype(float), 15.0).astype("float32"), (n_prof, 1))

    # Argo stores JULD as days since 1950-01-01
    juld = np.array([(START + pd.Timedelta(days=r[2]) - pd.Timestamp("1950-01-01"))
                     / pd.Timedelta(days=1) for r in rows])
    qc = np.full((n_prof, n_lev), b"1", dtype="S1")

    ds = xr.Dataset(
        {
            "PLATFORM_NUMBER": (("N_PROF",), np.array([r[0] for r in rows], dtype="S8")),
            "CYCLE_NUMBER": (("N_PROF",), np.array([r[1] for r in rows], dtype="int32")),
            "JULD": (("N_PROF",), juld, {
                "long_name": "Julian day (UTC) of the station relative to REFERENCE_DATE_TIME",
                "units": "days since 1950-01-01 00:00:00 UTC", "_FillValue": 999999.0}),
            "LATITUDE": (("N_PROF",), np.array([r[3] for r in rows]), {
                "units": "degree_north", "_FillValue": 99999.0}),
            "LONGITUDE": (("N_PROF",), np.array([r[4] for r in rows]), {
                "units": "degree_east", "_FillValue": 99999.0}),
            "PRES": (("N_PROF", "N_LEVELS"), pres, {
                "long_name": "Sea water pressure, equals 0 at sea-level",
                "units": "decibar", "_FillValue": 99999.0}),
            "TEMP": (("N_PROF", "N_LEVELS"), temp, {
                "long_name": "Sea temperature in-situ ITS-90 scale",
                "units": "degree_Celsius", "_FillValue": 99999.0}),
            "PSAL": (("N_PROF", "N_LEVELS"), psal, {
                "long_name": "Practical salinity", "units": "psu", "_FillValue": 99999.0}),
            "TEMP_QC": (("N_PROF", "N_LEVELS"), qc, {"long_name": "quality flag"}),
            "PSAL_QC": (("N_PROF", "N_LEVELS"), qc.copy(), {"long_name": "quality flag"}),
        },
        attrs={"title": "Synthetic Argo-style profiles, Arabian Sea, 2025 Q1",
               "institution": "none (synthetic)", "source": "make_synthetic_data.py",
               "comment": SYNTH_NOTE, "Conventions": "Argo-3.1 (style only)",
               "history": "generated offline"},
    )
    path = out / "argo_profiles" / "argo_synthetic_arabian_sea_2025Q1.nc"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)  # 99999 stays literal: _FillValue is an attribute, values are not masked
    return path, n_prof, n_lev


# ----------------------------------------------------- 2. Ship CTD CSV ----
def make_ship_ctd(out: Path, rng):
    depth = np.arange(0, 501, 2.0)
    frames = []
    lon_list = np.arange(62.0, 73.0, 1.5)  # eight stations along 15N
    lat = 15.0
    for i, lon in enumerate(lon_list):
        assert is_ocean(lat, lon)
        when = pd.Timestamp("2025-02-10T03:00:00") + pd.Timedelta(hours=9 * i)
        t, s = make_ts_profile(lat, lon, (when - START).days, depth, rng)
        p = gsw.p_from_z(-depth, lat)
        sa = gsw.SA_from_SP(s, p, lon, lat)
        ct = gsw.CT_from_t(sa, t, p)
        frames.append(pd.DataFrame({
            "cast_id": f"SYN-C{i + 1:03d}", "station": f"S{i + 1:02d}",
            "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lat": lat, "lon": round(lon, 4), "depth_m": depth,
            "temp_C": t.round(4), "sal_PSU": s.round(4),
            "sound_vel_m_s": gsw.sound_speed(sa, ct, p).round(3)}))
    df = pd.concat(frames, ignore_index=True)
    path = out / "ship_ctd" / "ctd_casts_synthetic_arabian_sea_2025.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        fh.write(f"# {SYNTH_NOTE}\n# cruise: SYN-CRUISE-2025-02\n# fill_value: -999\n")
        fh.write("# units: depth_m=m temp_C=degC sal_PSU=PSS-78 sound_vel_m_s=m/s\n")
        df.to_csv(fh, index=False, lineterminator="\n")
    return path, len(df)


# ------------------------------------------------------ 3. SST grid ------
def make_sst(out: Path, rng):
    lat = np.arange(LAT_MIN, LAT_MAX + 1e-6, 0.25)
    lon = np.arange(LON_MIN, LON_MAX + 1e-6, 0.25)
    time = pd.date_range(START, periods=NDAYS, freq="D")
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    land = globe.is_land(la, lo)
    f1 = ndimage.gaussian_filter(rng.normal(size=la.shape), 4)
    f2 = ndimage.gaussian_filter(rng.normal(size=la.shape), 4)
    f1, f2 = f1 / f1.std(), f2 / f2.std()
    cube = np.empty((NDAYS, lat.size, lon.size))
    for d in range(NDAYS):
        w = d / (NDAYS - 1)
        eddies = 0.5 * ((1 - w) * f1 + w * f2)
        field = sst_model(la, d) + eddies + 273.15  # stored in kelvin, like GHRSST
        field[land] = np.nan
        cube[d] = field
    ds = xr.Dataset(
        {"analysed_sst": (("time", "lat", "lon"), cube.astype("float32"), {
            "long_name": "analysed sea surface temperature", "standard_name": "sea_surface_temperature",
            "units": "kelvin"})},
        coords={"time": time, "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
                "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"})},
        attrs={"title": "Synthetic gridded SST, 0.25 deg daily", "Conventions": "CF-1.8",
               "comment": SYNTH_NOTE, "source": "make_synthetic_data.py"})
    path = out / "sst_grid" / "sst_synthetic_0p25deg_2025Q1.nc"
    path.parent.mkdir(parents=True, exist_ok=True)
    # packed int16 like GHRSST products: tests scale_factor/add_offset/_FillValue decoding
    ds.to_netcdf(path, encoding={"analysed_sst": {
        "dtype": "int16", "scale_factor": 0.01, "add_offset": 273.15, "_FillValue": -32768}})
    return path, cube.shape


# ---------------------------------------------- 4. Buoy met-ocean CSV ----
def make_buoy(out: Path, rng):
    times = pd.date_range(START, periods=NDAYS * 24, freq="h")
    n = len(times)
    lat, lon = 15.0, 68.0
    assert is_ocean(lat, lon)
    hours = np.arange(n)
    smooth = lambda scale: ndimage.gaussian_filter1d(rng.normal(size=n), 12) * scale  # noqa: E731
    wind_speed = np.clip(5.0 + 12 * smooth(1) + 0.8 * np.sin(2 * np.pi * hours / 24), 0.3, None)
    wind_dir = (50 + 120 * smooth(1)) % 360  # direction wind blows FROM, deg
    air_t = 26.0 + 1.5 * hours / n + 0.8 * np.sin(2 * np.pi * (hours - 14) / 24) + smooth(3)
    rh = np.clip(68 + 8 * smooth(4), 30, 99)
    pres = 1011 + 1.2 * np.sin(2 * np.pi * hours / 12) + smooth(8)
    hs = np.clip(1.2 + 0.18 * wind_speed - 0.8 + smooth(3), 0.2, None)
    tp = np.clip(8.0 + smooth(8), 4.0, 14.0)
    wave_dir = (210 + 40 * smooth(2)) % 360
    sst = sst_model(lat, hours / 24.0) + 0.2 * np.sin(2 * np.pi * (hours - 15) / 24)
    cur_speed = np.clip(18 + 10 * smooth(3), 1, None)
    cur_dir = (120 + 50 * smooth(2)) % 360
    df = pd.DataFrame({
        "time_utc": times.strftime("%Y-%m-%dT%H:%M:%SZ"), "lat": lat, "lon": lon,
        "wspd_ms": wind_speed.round(2), "wdir_deg": wind_dir.round(0),
        "airtemp_C": air_t.round(2), "rh_pct": rh.round(1), "slp_hPa": pres.round(1),
        "hs_m": hs.round(2), "tp_s": tp.round(1), "mwdir_deg": wave_dir.round(0),
        "sst_C": sst.round(2), "cur_speed_cm_s": cur_speed.round(1), "cur_dir_deg": cur_dir.round(0)})
    path = out / "buoy_met_wave" / "buoy_met_wave_synthetic_2025Q1.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        fh.write(f"# {SYNTH_NOTE}\n# platform: SYN-BUOY-01\n# fill_value: -999\n")
        df.to_csv(fh, index=False, lineterminator="\n")
    return path, n


# ------------------------------------------------------ 5. Bathymetry ----
def make_bathymetry(out: Path, rng):
    lat = np.round(np.arange(LAT_MIN, LAT_MAX + 1e-6, 0.1), 3)
    lon = np.round(np.arange(LON_MIN, LON_MAX + 1e-6, 0.1), 3)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    land = globe.is_land(la, lo)
    km = 11.1
    d_sea = ndimage.distance_transform_edt(~land) * 0.1 * km   # km from coast, over ocean
    d_land = ndimage.distance_transform_edt(land) * 0.1 * km   # km inland, over land
    relief = ndimage.gaussian_filter(rng.normal(size=la.shape), 5)
    relief = relief / relief.std()
    depth = 20 + 4300 * (1 - np.exp(-d_sea / 380.0)) + 150 * relief
    elev = np.where(land, 10 + 4.0 * d_land + 40 * relief, -np.clip(depth, 5, None))
    ds = xr.Dataset(
        {"elevation": (("lat", "lon"), elev.astype("int16"), {
            "long_name": "Elevation relative to sea level", "standard_name": "height_above_mean_sea_level",
            "units": "m", "comment": "negative = below sea level"})},
        coords={"lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
                "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"})},
        attrs={"title": "Synthetic bathymetry, 0.1 deg", "Conventions": "CF-1.8",
               "comment": SYNTH_NOTE, "source": "make_synthetic_data.py"})
    path = out / "bathymetry" / "bathymetry_synthetic_0p1deg.nc"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)
    return path, elev.shape


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/raw", type=Path)
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    for fn in (make_argo, make_ship_ctd, make_sst, make_buoy, make_bathymetry):
        path, *info = fn(args.out, rng)
        print(f"wrote {path}  {info}  ({path.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
