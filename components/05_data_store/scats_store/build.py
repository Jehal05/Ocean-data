"""Turn the staging dataset plus the review decisions into the dataset that is written to Zarr."""
from __future__ import annotations

import math

import numpy as np
import xarray as xr


def _json_safe(x):
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, np.ndarray):
        return _json_safe(x.tolist())
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return str(x)
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    return str(x)


def apply_human_flags(ds: xr.Dataset, rows) -> xr.Dataset:
    """Copy of ds with the people's decisions written into the <variable>_qc arrays.

    rows: iterable of (source_record, variable, level_index, flag) for human rows at the review snapshot.
    Data values are never touched. Gridded datasets take no cell-level decisions (docs/phase3_design.md section 1).
    """
    out = ds.copy(deep=True)
    kind = out.attrs["dataset_type"]
    for rec, var, level, flag in rows:
        name = f"{var}_qc"
        if name not in out:
            raise KeyError(f"no flag array {name}")
        if kind in ("grid", "bathymetry"):
            continue
        arr = out[name].values.copy()
        if kind == "profile" and var not in ("position", "time"):
            arr[rec, level] = flag
        else:
            arr[rec] = flag
        out[name] = (out[name].dims, arr, out[name].attrs)
    return out


def for_zarr(ds: xr.Dataset, meta: dict) -> xr.Dataset:
    """Fixed-width strings instead of Python objects, JSON-safe attributes, review facts in the attributes."""
    out = ds.copy(deep=True)
    for name in list(out.variables):
        v = out[name]
        if v.dtype.kind == "O":
            width = max((len(str(x)) for x in v.values.ravel()), default=1) or 1
            out[name] = (v.dims, v.values.astype(f"U{width}"), v.attrs)
        out[name].attrs = _json_safe(dict(out[name].attrs))
    out.attrs = _json_safe({**dict(ds.attrs), **meta})
    return out
