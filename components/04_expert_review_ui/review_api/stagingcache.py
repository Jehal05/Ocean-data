"""Staging NetCDF files, kept in memory until the file changes (path, mtime and size are the cache key)."""
from __future__ import annotations

from fastapi import HTTPException

from scats_ingest.staging import read_staging, staging_path

_CACHE: dict = {}


def load(staging_dir, dataset_key):
    p = staging_path(staging_dir, dataset_key)
    try:
        st = p.stat()
    except OSError:
        raise HTTPException(503, "the staging file of this dataset is missing; re-ingest it")
    k = (str(p), st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(str(p))
    if hit and hit[0] == k:
        return hit[1]
    try:
        ds = read_staging(staging_dir, dataset_key)
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "the staging file of this dataset cannot be read")
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[str(p)] = (k, ds)
    return ds
