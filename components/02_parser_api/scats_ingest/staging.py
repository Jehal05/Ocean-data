"""Staging store: the standardised, QC-flagged dataset as a NetCDF file (Phase 3 moves this to Zarr)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import xarray as xr


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def staging_path(staging_dir: Path | str, dataset_key: str) -> Path:
    return Path(staging_dir) / f"{dataset_key}.nc"


def write_staging(ds: xr.Dataset, staging_dir: Path | str, dataset_key: str) -> Path:
    out = staging_path(staging_dir, dataset_key)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".nc.tmp")
    ds.to_netcdf(tmp)
    os.replace(tmp, out)                      # atomic: readers never see half a file
    return out


def read_staging(staging_dir: Path | str, dataset_key: str) -> xr.Dataset:
    ds = xr.open_dataset(staging_path(staging_dir, dataset_key)).load()
    ds.close()
    return ds
