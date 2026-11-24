"""Unit conversion for the units seen in the supported sources.

Only the conversions listed here exist. An unknown pair raises UnitError rather than guessing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KNOT_IN_MS = 1852.0 / 3600.0  # 1 knot = 1 nautical mile (1852 m) per hour

_ALIASES = {
    "degc": "degC", "degree_celsius": "degC", "degrees_celsius": "degC", "celsius": "degC", "c": "degC",
    "degree_c": "degC", "deg_c": "degC",
    "kelvin": "K", "k": "K",
    "1": "1", "psu": "1", "pss-78": "1", "pss78": "1", "dimensionless": "1",
    "dbar": "dbar", "decibar": "dbar",
    "m s-1": "m s-1", "m/s": "m s-1", "m.s-1": "m s-1", "ms-1": "m s-1", "meter_per_second": "m s-1",
    "cm s-1": "cm s-1", "cm/s": "cm s-1", "cm.s-1": "cm s-1",
    "knot": "knot", "knots": "knot", "kt": "knot", "kn": "knot",
    "degree": "degree", "degrees": "degree", "deg": "degree", "degree_true": "degree",
    "%": "%", "percent": "%", "pct": "%",
    "hpa": "hPa", "mbar": "hPa", "millibar": "hPa",
    "m": "m", "meter": "m", "metre": "m", "meters": "m", "metres": "m",
    "s": "s", "sec": "s", "second": "s", "seconds": "s",
    "kg m-3": "kg m-3", "kg/m3": "kg m-3",
}

_CONVERSIONS = {
    ("K", "degC"): lambda v: v - 273.15,
    ("degC", "K"): lambda v: v + 273.15,
    ("cm s-1", "m s-1"): lambda v: v / 100.0,
    ("knot", "m s-1"): lambda v: v * KNOT_IN_MS,
}


class UnitError(ValueError):
    pass


def canonical(units: str) -> str:
    """Normalise a unit spelling to the spelling used in the data model."""
    key = str(units).strip().lower()
    if key not in _ALIASES:
        raise UnitError(f"unknown unit '{units}'")
    return _ALIASES[key]


def convert(values, src: str, dst: str):
    """Convert values from unit src to unit dst (both may use any known alias)."""
    s, d = canonical(src), canonical(dst)
    if s == d:
        return np.asarray(values, dtype="float64") if not np.isscalar(values) else float(values)
    try:
        fn = _CONVERSIONS[(s, d)]
    except KeyError:
        raise UnitError(f"no conversion from '{src}' to '{dst}'") from None
    return fn(np.asarray(values, dtype="float64")) if not np.isscalar(values) else float(fn(float(values)))


def elevation_to_depth(elevation):
    """GEBCO-style elevation (negative below sea level) to depth (positive down)."""
    return -np.asarray(elevation, dtype="float64")


def lon_to_pm180(lon):
    """0..360 longitude to -180..180. Values outside 0..360 are left unchanged so an invalid
    longitude stays visible to the position_range test instead of being wrapped into range."""
    lon = np.asarray(lon, dtype="float64")
    return np.where((lon > 180.0) & (lon <= 360.0), lon - 360.0, lon)


def argo_juld_to_datetime(juld, reference: str = "1950-01-01"):
    """Argo JULD (days since reference, UTC) to datetime64[ns]. NaN gives NaT."""
    juld = np.asarray(juld, dtype="float64")
    ref = pd.Timestamp(reference)
    return (ref + pd.to_timedelta(juld, unit="D")).to_numpy() if juld.ndim else (ref + pd.to_timedelta(juld, unit="D"))


def parse_iso_utc(values):
    """ISO 8601 strings to datetime64[ns] in UTC (timezone dropped). Unparseable gives NaT."""
    parsed = pd.to_datetime(pd.Series(values, dtype="object"), utc=True, errors="coerce")
    return parsed.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
