"""The product definitions as pure functions (numpy in, xarray out). No database, no storage, no clock.

Rules for every product: only values with a good flag (1 or 2) and a finite number are used; a bin with fewer than
`min_obs` such values is missing (NaN) and its count is still reported; nothing is interpolated, smoothed or
extrapolated. Missing counts are 0.
"""
from __future__ import annotations

import numpy as np
import xarray as xr

AUX_VARS = ("position", "time")


def data_variables(ds: xr.Dataset) -> list[str]:
    """Variables that have a <name>_qc partner, except the position and time flags themselves."""
    return [n for n in ds.data_vars if f"{n}_qc" in ds.variables and n not in AUX_VARS]


def good(values: np.ndarray, qc: np.ndarray, good_flags) -> np.ndarray:
    return np.isfinite(values) & np.isin(qc, list(good_flags))


def _mean(sum_, count, min_obs):
    with np.errstate(invalid="ignore", divide="ignore"):
        out = sum_ / np.where(count > 0, count, 1)
    return np.where(count >= max(int(min_obs), 1), out, np.nan)


# ------------------------------------------------------------ profiles --

def binned_profile(ds: xr.Dataset, *, edges, variables, good_flags, min_obs: int) -> xr.Dataset:
    edges = np.asarray(edges, dtype="float64")
    nb, npro = len(edges) - 1, ds.sizes["profile"]
    depth = np.asarray(ds["depth"].values, dtype="float64")                       # (profile, level)
    idx = np.searchsorted(edges, depth, side="right") - 1
    in_range = np.isfinite(depth) & (idx >= 0) & (idx < nb)
    usable = good(np.zeros(npro), ds["position_qc"].values, good_flags) & good(np.zeros(npro), ds["time_qc"].values, good_flags)
    out = xr.Dataset(coords={"bin": np.arange(nb)})
    for k in ("time", "latitude", "longitude", "platform_id", "source_record"):
        out[k] = ("profile", np.asarray(ds[k].values).copy(), dict(ds[k].attrs))
    out["profile_usable"] = ("profile", usable.astype("int8"), {"long_name": "1 when the position and time flags are good"})
    out["bin_top"] = ("bin", edges[:-1], {"units": "m", "long_name": "top of the depth bin (included)"})
    out["bin_bottom"] = ("bin", edges[1:], {"units": "m", "long_name": "bottom of the depth bin (excluded)"})
    out["depth"] = ("bin", (edges[:-1] + edges[1:]) / 2, {"units": "m", "long_name": "bin centre depth"})
    rows = np.repeat(np.arange(npro)[:, None], depth.shape[1], axis=1)
    for name in variables:
        if name not in ds.variables:
            continue
        v = np.asarray(ds[name].values, dtype="float64")
        ok = good(v, ds[f"{name}_qc"].values, good_flags) & in_range & usable[:, None]
        flat = (rows * nb + idx)[ok]
        count = np.bincount(flat, minlength=npro * nb).reshape(npro, nb)
        total = np.bincount(flat, weights=v[ok], minlength=npro * nb).reshape(npro, nb)
        out[name] = (("profile", "bin"), _mean(total, count, min_obs), {**_units(ds[name]), "long_name": f"mean of good {name} in the bin"})
        out[f"{name}_n_obs"] = (("profile", "bin"), count.astype("int32"), {"long_name": f"number of good {name} values in the bin"})
    return out


# -------------------------------------------------------------- series --

def _units(var) -> dict:
    return {"units": var.attrs["units"]} if "units" in var.attrs else {}


def binned_series(ds: xr.Dataset, *, unit: str, good_flags, min_obs: int, circular_units) -> xr.Dataset:
    """unit 'h' (hour) or 'D' (day), UTC. Axis: platform x complete run of bins from the first to the last used record."""
    time = ds["time"].values.astype("datetime64[ns]")
    used = ~np.isnat(time)
    used &= good(np.zeros(len(time)), ds["time_qc"].values, good_flags) & good(np.zeros(len(time)), ds["position_qc"].values, good_flags)
    plats, pidx = np.unique(np.asarray(ds["platform_id"].values).astype(str), return_inverse=True)
    if not used.any():
        raise ValueError("no record has a good time and position flag")
    bins = time.astype(f"datetime64[{unit}]")
    first, last = bins[used].min(), bins[used].max()
    axis = np.arange(first, last + 1, dtype=f"datetime64[{unit}]")
    nt, npl = len(axis), len(plats)
    tix = (bins - first).astype("int64")
    sel = used
    cell = (pidx * nt + tix)[sel]
    out = xr.Dataset(coords={"platform": plats, "time": axis.astype("datetime64[ns]")})
    lat = np.full(npl, np.nan)
    lon = np.full(npl, np.nan)
    for i in range(npl):
        m = sel & (pidx == i)
        lat[i], lon[i] = np.mean(ds["latitude"].values[m]), np.mean(ds["longitude"].values[m])
    out["latitude"] = ("platform", lat, {"units": "degrees_north", "long_name": "mean position of the records used"})
    out["longitude"] = ("platform", lon, {"units": "degrees_east", "long_name": "mean position of the records used"})
    for name in data_variables(ds):
        v = np.asarray(ds[name].values, dtype="float64")
        ok = good(v, ds[f"{name}_qc"].values, good_flags)[sel]
        c = np.bincount(cell[ok], minlength=npl * nt)
        count = c.reshape(npl, nt)
        circular = ds[name].attrs.get("units") in set(circular_units)
        if circular:
            rad = np.deg2rad(v[sel][ok])
            s = np.bincount(cell[ok], weights=np.sin(rad), minlength=npl * nt).reshape(npl, nt)
            co = np.bincount(cell[ok], weights=np.cos(rad), minlength=npl * nt).reshape(npl, nt)
            with np.errstate(invalid="ignore"):
                ang = np.mod(np.rad2deg(np.arctan2(s, co)), 360.0)
            ang = np.where(np.isclose(ang, 360.0, atol=1e-9), 0.0, ang)
            out[f"{name}_mean"] = (("platform", "time"), np.where(count >= max(int(min_obs), 1), ang, np.nan),
                                   {**_units(ds[name]), "long_name": f"circular mean of good {name}"})
        else:
            total = np.bincount(cell[ok], weights=v[sel][ok], minlength=npl * nt).reshape(npl, nt)
            lo = np.full(npl * nt, np.inf)
            hi = np.full(npl * nt, -np.inf)
            np.minimum.at(lo, cell[ok], v[sel][ok])
            np.maximum.at(hi, cell[ok], v[sel][ok])
            enough = count >= max(int(min_obs), 1)
            out[f"{name}_mean"] = (("platform", "time"), _mean(total, count, min_obs), {**_units(ds[name]), "long_name": f"mean of good {name}"})
            out[f"{name}_min"] = (("platform", "time"), np.where(enough, lo.reshape(npl, nt), np.nan), {**_units(ds[name]), "long_name": f"minimum of good {name}"})
            out[f"{name}_max"] = (("platform", "time"), np.where(enough, hi.reshape(npl, nt), np.nan), {**_units(ds[name]), "long_name": f"maximum of good {name}"})
        out[f"{name}_count"] = (("platform", "time"), count.astype("int32"), {"long_name": f"number of good {name} values in the bin"})
    return out


# ------------------------------------------------------------ SST grid --

def monthly_mean(ds: xr.Dataset, *, variable: str, good_flags, min_obs: int) -> xr.Dataset:
    time = ds["time"].values.astype("datetime64[ns]")
    ok_t = ~np.isnat(time) & good(np.zeros(len(time)), ds["time_qc"].values, good_flags)
    months = time.astype("datetime64[M]")
    uniq = np.unique(months[ok_t])
    v = np.asarray(ds[variable].values, dtype="float64")
    g = good(v, ds[f"{variable}_qc"].values, good_flags) & ok_t[:, None, None]
    mean = np.full((len(uniq),) + v.shape[1:], np.nan)
    count = np.zeros((len(uniq),) + v.shape[1:], dtype="int16")
    for i, m in enumerate(uniq):
        sel = months == m
        c = g[sel].sum(axis=0)
        s = np.where(g[sel], v[sel], 0.0).sum(axis=0)
        count[i] = c
        mean[i] = _mean(s, c, min_obs)
    dims = ("time", "latitude", "longitude")
    out = xr.Dataset(coords={"time": uniq.astype("datetime64[ns]"), "latitude": ds["latitude"].values, "longitude": ds["longitude"].values})
    out["latitude"].attrs, out["longitude"].attrs = dict(ds["latitude"].attrs), dict(ds["longitude"].attrs)
    out[variable] = (dims, mean, {**_units(ds[variable]), "long_name": f"monthly mean of good {variable}"})
    out[f"{variable}_n_obs"] = (dims, count, {"long_name": "number of good daily values in the month"})
    return out
