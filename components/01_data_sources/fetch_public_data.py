#!/usr/bin/env python3
"""Fetch REAL public data for the four Phase 1 dataset types.

STATUS: written but NOT tested against the live servers. It was authored in
a sandbox with no access to these hosts. Dataset IDs and variable names below
are from memory of the public services; if a request fails, the script says
which one and prints the URL so you can fix it in the browser first.
Use make_synthetic_data.py when you are offline or a source is down.

Region : 8-24N, 60-75E          Window : 2025-01-01 .. 2025-03-31
Needs  : internet. Only the ERA5 step needs a (free) login.

  1. Argo profiles  -> NOAA/IFREMER ERDDAP tabledap        (CSV)
  2. SST grid       -> NOAA OISST via CoastWatch ERDDAP    (NetCDF)
  3. Waves / wind   -> ERA5 via Copernicus CDS             (NetCDF)  [login]
  4. Bathymetry     -> ETOPO via CoastWatch ERDDAP         (NetCDF)
     (GEBCO is the better source: https://download.gebco.net , manual download)

Usage:  python fetch_public_data.py --out data/raw [--only argo sst era5 bathy]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

BBOX = dict(lat0=8, lat1=24, lon0=60, lon1=75)
T0, T1 = "2025-01-01T00:00:00Z", "2025-03-31T23:59:59Z"

ERDDAP_ARGO = "https://erddap.ifremer.fr/erddap/tabledap/ArgoFloats.csv"
ERDDAP_SST = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/ncdcOisst21Agg_LonPM180.nc"
ERDDAP_TOPO = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.nc"


def download(url: str, dest: Path, label: str) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        print(f"OK    {label:10s} {dest}  ({dest.stat().st_size / 1024:.0f} KiB)")
        return True
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        print(f"FAIL  {label:10s} {exc}\n      url: {url}")
        return False


def fetch_argo(out: Path) -> bool:
    cols = "platform_number,cycle_number,time,latitude,longitude,pres,temp,psal,temp_qc,psal_qc"
    q = (f"?{cols}&time>={T0}&time<={T1}&latitude>={BBOX['lat0']}&latitude<={BBOX['lat1']}"
         f"&longitude>={BBOX['lon0']}&longitude<={BBOX['lon1']}&pres<=1000&orderBy(\"platform_number,time,pres\")")
    return download(ERDDAP_ARGO + q, out / "argo_profiles" / "argo_erddap_arabian_sea_2025Q1.csv", "argo")


def fetch_sst(out: Path) -> bool:
    q = (f"?sst[(2025-01-01T12:00:00Z):1:(2025-03-31T12:00:00Z)][(0.0):1:(0.0)]"
         f"[({BBOX['lat0']}):1:({BBOX['lat1']})][({BBOX['lon0']}):1:({BBOX['lon1']})]")
    return download(ERDDAP_SST + q, out / "sst_grid" / "oisst_arabian_sea_2025Q1.nc", "sst")


def fetch_bathy(out: Path) -> bool:
    q = f"?altitude[({BBOX['lat0']}):1:({BBOX['lat1']})][({BBOX['lon0']}):1:({BBOX['lon1']})]"
    return download(ERDDAP_TOPO + q, out / "bathymetry" / "etopo180_arabian_sea.nc", "bathy")


def fetch_era5(out: Path) -> bool:
    """ERA5 needs a free account: https://cds.climate.copernicus.eu , then ~/.cdsapirc ."""
    dest = out / "buoy_met_wave" / "era5_wind_wave_arabian_sea_2025Q1.nc"
    try:
        import cdsapi  # type: ignore
        client = cdsapi.Client()
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP  era5       cdsapi not usable ({exc}).\n"
              "      MANUAL: sign in at https://cds.climate.copernicus.eu, open dataset\n"
              "      'ERA5 hourly data on single levels from 1940 to present', pick variables\n"
              "      10m u/v wind, 2m temperature, 2m dewpoint temperature, mean sea level pressure,\n"
              "      significant height of combined wind waves and swell, peak wave period,\n"
              "      mean wave direction, sea surface temperature; area N24 W60 S8 E75;\n"
              f"      Jan-Mar 2025, NetCDF. Save as: {dest}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.retrieve("reanalysis-era5-single-levels", {
            "product_type": ["reanalysis"], "data_format": "netcdf",
            "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind", "2m_temperature",
                         "2m_dewpoint_temperature", "mean_sea_level_pressure",
                         "significant_height_of_combined_wind_waves_and_swell", "peak_wave_period",
                         "mean_wave_direction", "sea_surface_temperature"],
            "year": ["2025"], "month": ["01", "02", "03"],
            "day": [f"{d:02d}" for d in range(1, 32)], "time": [f"{h:02d}:00" for h in range(0, 24, 3)],
            "area": [BBOX["lat1"], BBOX["lon0"], BBOX["lat0"], BBOX["lon1"]]}, str(dest))
        print(f"OK    era5       {dest}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  era5       {exc}")
        return False


STEPS = {"argo": fetch_argo, "sst": fetch_sst, "era5": fetch_era5, "bathy": fetch_bathy}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/raw", type=Path)
    ap.add_argument("--only", nargs="+", choices=STEPS, default=list(STEPS))
    a = ap.parse_args()
    results = {name: STEPS[name](a.out) for name in a.only}
    failed = [n for n, ok in results.items() if not ok]
    if failed:
        print(f"\n{len(failed)} source(s) not fetched: {', '.join(failed)}\n"
              "Offline fallback: python components/01_data_sources/make_synthetic_data.py")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
