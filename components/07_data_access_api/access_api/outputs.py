"""Rows -> JSON values, CSV and NetCDF. Safe by construction: no NaN in JSON, spreadsheet formulas neutralised in CSV,
file names made by the server."""
from __future__ import annotations

import io
import os
import re
import tempfile

import numpy as np
import pandas as pd
import xarray as xr

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def neutralise(s: str) -> str:
    """A text cell that a spreadsheet would run as a formula gets a leading apostrophe. Numbers are not text cells."""
    return "'" + s if s.startswith(FORMULA_START) else s


def _iso(t: np.ndarray) -> np.ndarray:
    t = np.asarray(t).astype("datetime64[ns]")
    return np.where(np.isnat(t), "", np.datetime_as_string(t, unit="s")).astype(str) if t.size else t.astype(str)


def text_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Times as ISO strings (UTC, with Z); everything else unchanged."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype.kind == "M":
            vals = _iso(out[c].values)
            out[c] = [v + "Z" if v else "" for v in vals]
    return out


def json_rows(df: pd.DataFrame) -> list[dict]:
    t = text_frame(df)
    rows = []
    for rec in t.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if isinstance(v, (np.generic,)):
                v = v.item()
            if isinstance(v, float) and not np.isfinite(v):
                v = None
            clean[k] = v
        rows.append(clean)
    return rows


def csv_bytes(df: pd.DataFrame) -> bytes:
    t = text_frame(df)
    for c in t.columns:
        if t[c].dtype.kind in "OUS":
            t[c] = [neutralise(str(x)) for x in t[c].tolist()]
    buf = io.StringIO()
    t.to_csv(buf, index=False, lineterminator="\n")
    return buf.getvalue().encode("utf-8")


def netcdf_bytes(df: pd.DataFrame, attrs: dict, units: dict) -> bytes:
    ds = xr.Dataset(attrs={k: ("" if v is None else v) for k, v in attrs.items()})
    for c in df.columns:
        col = df[c]
        vals = col.values.astype("U") if col.dtype.kind in "OUS" else col.values
        ds[c] = ("row", vals, {"units": units[c]} if units.get(c) else {})
    fd, path = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        ds.to_netcdf(path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def safe_filename(key: str, version: int | None, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]
    return f"{base}{'_v' + str(version) if version else ''}.{ext}"
