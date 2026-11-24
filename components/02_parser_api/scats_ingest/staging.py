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


def write_temp_staging(ds: xr.Dataset, staging_dir: Path | str, dataset_key: str) -> Path:
    """Write next to the final file under a temporary name. Nothing visible to readers changes yet."""
    out = staging_path(staging_dir, dataset_key)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.tmp{os.getpid()}")
    ds.to_netcdf(tmp)
    return tmp


def commit_staging(tmp: Path, staging_dir: Path | str, dataset_key: str) -> Path:
    """Atomic rename into place. Call ONLY after the database transaction has committed."""
    out = staging_path(staging_dir, dataset_key)
    os.replace(tmp, out)
    return out


def discard_temp_staging(tmp: Path | None) -> None:
    if tmp is not None:
        Path(tmp).unlink(missing_ok=True)


def read_staging(staging_dir: Path | str, dataset_key: str) -> xr.Dataset:
    ds = xr.open_dataset(staging_path(staging_dir, dataset_key)).load()
    ds.close()
    return ds
