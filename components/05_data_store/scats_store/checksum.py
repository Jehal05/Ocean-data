"""Checksums that do not depend on how an array was stored (NetCDF, Zarr, strings as object or fixed width)."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import xarray as xr


def _canon(a: np.ndarray) -> tuple[str, bytes]:
    k = a.dtype.kind
    if k in "OUS":
        return "str", "\x00".join(str(x) for x in a.ravel().tolist()).encode()
    if k == "M":
        return "datetime64[ns]", a.astype("datetime64[ns]").view("int64").tobytes()
    if k == "f":
        a = np.where(np.isnan(a), np.nan, a)          # one NaN bit pattern
    return a.dtype.str, np.ascontiguousarray(a).tobytes()


def content_sha256(ds: xr.Dataset) -> str:
    """Over every variable's name, dims, shape, type and values (data AND flags). Attributes are not included."""
    h = hashlib.sha256()
    for name in sorted(ds.variables):
        v = ds.variables[name]
        kind, raw = _canon(np.asarray(v.values))
        h.update(json.dumps([name, list(v.dims), list(v.shape), kind]).encode())
        h.update(raw)
    return h.hexdigest()


def manifest_sha256(objects: dict[str, tuple[str, int]]) -> str:
    """objects: relative path -> (sha256, size). Canonical JSON, so the same objects always give the same value."""
    return hashlib.sha256(json.dumps(sorted([k, v[0], v[1]] for k, v in objects.items())).encode()).hexdigest()


def datasets_equal(a: xr.Dataset, b: xr.Dataset) -> list[str]:
    """Differences between two datasets (values, dims, variable names). Empty list = identical data."""
    out = []
    if set(a.variables) != set(b.variables):
        out.append(f"variables differ: {sorted(set(a.variables) ^ set(b.variables))}")
    for n in sorted(set(a.variables) & set(b.variables)):
        va, vb = a.variables[n], b.variables[n]
        if va.dims != vb.dims or va.shape != vb.shape:
            out.append(f"{n}: dims/shape {va.dims}{va.shape} vs {vb.dims}{vb.shape}")
            continue
        ka, ra = _canon(np.asarray(va.values))
        kb, rb = _canon(np.asarray(vb.values))
        if ka != kb or ra != rb:
            out.append(f"{n}: values differ")
    return out
