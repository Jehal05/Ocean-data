"""Subsetting: a Filter plus a lazily opened Zarr dataset give a Plan (how many rows, which indices) and then rows.

Reading is lazy. Counting rows needs only the small coordinate-like arrays (time, position, platform, depth); the data
arrays are read, chunk by chunk, only for the rows of the page or download that is being produced.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import xarray as xr

from .filters import GOOD_FLAGS, Filter

AUX = ("position", "time")
CONTEXT = {"time", "latitude", "longitude", "platform_id", "source_record", "profile_usable", "bin_top", "bin_bottom", "depth", "platform"}


def layout_of(ds: xr.Dataset) -> str:
    if "profile" in ds.dims:
        return "profile"
    if "record" in ds.dims:
        return "records"
    if "platform" in ds.dims and "time" in ds.dims:
        return "series"
    if "latitude" in ds.dims and "longitude" in ds.dims:
        return "grid"
    raise ValueError("unsupported dataset layout")


def data_vars(ds: xr.Dataset, is_product: bool) -> list[str]:
    if is_product:
        return [n for n in ds.data_vars if n not in CONTEXT]
    return [n for n in ds.data_vars if f"{n}_qc" in ds.variables and n not in AUX]


def axes_for(dataset_type: str) -> dict:
    """Which filters make sense, from the catalogue's dataset type (no need to open the store)."""
    return {"time": dataset_type in ("profile", "timeseries", "grid"), "depth": dataset_type == "profile",
            "platform": dataset_type in ("profile", "timeseries")}


def _good(qc: np.ndarray) -> np.ndarray:
    return np.isin(qc, GOOD_FLAGS)


def _time_mask(t: np.ndarray, f: Filter) -> np.ndarray:
    t = np.asarray(t).astype("datetime64[ns]")
    m = ~np.isnat(t)
    if f.time_from is not None:
        m &= t >= f.time_from
    if f.time_to is not None:
        m &= t <= f.time_to
    return m


def _platform_mask(ids: np.ndarray, f: Filter) -> np.ndarray:
    if not f.platforms:
        return np.ones(len(ids), dtype=bool)
    return np.isin(np.asarray(ids).astype(str), list(f.platforms))


@dataclass
class Plan:
    layout: str
    n_rows: int
    index: dict            # dim name -> int array of the selected indices (for grids and series: the axes, not per row)
    rows: dict | None      # profile / records: per-row index arrays; None for grids and series
    context: list[str]
    columns: list[str]
    is_product: bool
    quality: str
    level_dim: str | None = None

    def row_index(self, start: int, stop: int) -> dict[str, np.ndarray]:
        stop = min(stop, self.n_rows)
        if self.rows is not None:
            return {d: a[start:stop] for d, a in self.rows.items()}
        lin = np.arange(start, stop)
        dims = list(self.index)
        sizes = [len(self.index[d]) for d in dims]
        parts = np.unravel_index(lin, sizes) if sizes else ()
        return {d: self.index[d][p] for d, p in zip(dims, parts)}


def make_plan(ds: xr.Dataset, f: Filter, *, is_product: bool) -> Plan:
    lay = layout_of(ds)
    names = list(f.variables) if f.variables else data_vars(ds, is_product)
    quality_good = (f.quality == "good") and not is_product
    columns = []
    for n in names:
        columns.append(n)
        if not is_product and f"{n}_qc" in ds.variables:
            columns.append(f"{n}_qc")
    if lay in ("profile", "records"):
        t = ds["time"].values if "time" in ds.variables else None
        lat, lon = ds["latitude"].values, ds["longitude"].values
        n = len(lat)
        m = np.ones(n, dtype=bool)
        if t is not None:
            m &= _time_mask(t, f)
        if f.bbox is not None:
            m &= f.bbox.mask(lat, lon)
        if "platform_id" in ds.variables:
            m &= _platform_mask(ds["platform_id"].values, f)
        if quality_good and "position_qc" in ds.variables:
            m &= _good(ds["position_qc"].values) & _good(ds["time_qc"].values)
        if lay == "profile":
            depth = np.asarray(ds["depth"].values, dtype="float64")
            level_dim = "level" if depth.ndim == 2 else "bin"
            d2 = depth if depth.ndim == 2 else np.broadcast_to(depth, (n, len(depth)))
            ok = m[:, None] & np.isfinite(d2)
            if f.depth_min is not None:
                ok &= d2 >= f.depth_min
            if f.depth_max is not None:
                ok &= d2 <= f.depth_max
            pi, li = np.nonzero(ok)
            ctx = ["platform_id", "source_record", "time", "latitude", "longitude", level_dim, "depth"] + [c for c in ("bin_top", "bin_bottom") if c in ds.variables]
            return Plan("profile", int(len(pi)), {}, {"profile": pi, level_dim: li}, ctx, columns, is_product, f.quality, level_dim)
        idx = np.nonzero(m)[0]
        return Plan("records", int(len(idx)), {}, {"record": idx}, ["platform_id", "source_record", "time", "latitude", "longitude"], columns, is_product, f.quality)
    if lay == "series":
        lat, lon = ds["latitude"].values, ds["longitude"].values
        pm = _platform_mask(ds["platform"].values, f)
        if f.bbox is not None:
            pm &= f.bbox.mask(lat, lon)
        tm = _time_mask(ds["time"].values, f)
        pidx, tidx = np.nonzero(pm)[0], np.nonzero(tm)[0]
        return Plan("series", int(len(pidx) * len(tidx)), {"platform": pidx, "time": tidx}, None, ["platform", "time", "latitude", "longitude"], columns, is_product, f.quality)
    # grid
    la, lo = ds["latitude"].values, ds["longitude"].values
    ym = np.ones(len(la), dtype=bool)
    xm = np.ones(len(lo), dtype=bool)
    if f.bbox is not None:
        ym = (la >= f.bbox.south) & (la <= f.bbox.north)
        xm = f.bbox.lon_mask(lo)
    index = {}
    ctx = ["latitude", "longitude"]
    if "time" in ds.dims:
        tm = _time_mask(ds["time"].values, f)
        if quality_good and "time_qc" in ds.variables:
            tm &= _good(ds["time_qc"].values)
        index["time"] = np.nonzero(tm)[0]
        ctx = ["time"] + ctx
    index["latitude"], index["longitude"] = np.nonzero(ym)[0], np.nonzero(xm)[0]
    n = int(np.prod([len(v) for v in index.values()]))
    return Plan("grid", n, index, None, ctx, columns, is_product, f.quality)


def _gather(ds: xr.Dataset, name: str, rows: dict[str, np.ndarray]) -> np.ndarray:
    """Values of one variable at the given per-row indices. Reads the smallest block that holds them (lazy, chunk-wise)."""
    v = ds[name]
    dims = [d for d in v.dims]
    if not dims:
        return np.asarray(v.values)
    uniq = {d: np.unique(rows[d]) for d in dims}
    block = v.isel({d: uniq[d] for d in dims}).values
    pos = tuple(np.searchsorted(uniq[d], rows[d]) for d in dims)
    return block[pos]


def table(ds: xr.Dataset, plan: Plan, start: int, stop: int) -> pd.DataFrame:
    """Rows start..stop of the selection as a table (plain numpy values; times as datetime64)."""
    rows = plan.row_index(start, stop)
    data: dict[str, np.ndarray] = {}
    for c in plan.context:
        if c == plan.level_dim:
            data[c] = rows[c]
        elif c == "depth" and plan.layout == "profile":
            d = ds["depth"]
            data[c] = _gather(ds, "depth", rows) if d.ndim == 2 else d.values[rows[plan.level_dim]]
        elif c in ("bin_top", "bin_bottom"):
            data[c] = ds[c].values[rows[plan.level_dim]]
        else:
            data[c] = _gather(ds, c, rows) if c in ds.variables else np.full(len(next(iter(rows.values()))), np.nan)
    quality_good = plan.quality == "good" and not plan.is_product
    for c in plan.columns:
        vals = _gather(ds, c, rows)
        if quality_good and not c.endswith("_qc") and f"{c}_qc" in ds.variables:
            qc = _gather(ds, f"{c}_qc", rows)
            vals = np.where(_good(qc), vals, np.nan)
        data[c] = vals
    return pd.DataFrame(data)
