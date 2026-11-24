"""Where a dataset goes and how it is chunked (docs/phase3_design.md section 1 and 6)."""
from __future__ import annotations

import math

TARGET_CHUNK_BYTES = 8 * 2**20          # aim for about 8 MiB per chunk
MAX_CHUNK_BYTES = 50 * 2**20            # hard cap, asserted
TILE = 512                              # largest map tile edge for grids
ZARR_FORMAT = 2
COMPRESSION = {"codec": "blosc", "cname": "zstd", "clevel": 5, "shuffle": "byte"}   # lossless


def store_prefix(platform_type: str, dataset_key: str) -> str:
    return f"{platform_type}/{dataset_key}.zarr"


def chunks_for(dataset_type: str, dims: tuple, shape: tuple, itemsize: int) -> tuple:
    """Chunk shape for one array. Follows the way the data is read: one profile, one time window, one map tile."""
    if not shape:
        return ()
    size = max(int(itemsize), 1)
    if dataset_type == "profile" and len(shape) == 2:                 # (profile, level): whole profiles
        rows = max(1, min(shape[0], TARGET_CHUNK_BYTES // max(shape[1] * size, 1)))
        out = (rows, shape[1])
    elif dataset_type == "grid" and len(shape) == 3:                  # (time, lat, lon): tiles, then as many slices as fit
        la, lo = min(shape[1], TILE), min(shape[2], TILE)
        out = (max(1, min(shape[0], TARGET_CHUNK_BYTES // max(la * lo * size, 1))), la, lo)
    elif dataset_type == "bathymetry" and len(shape) == 2:
        out = (min(shape[0], TILE), min(shape[1], TILE))
    elif len(shape) == 1:                                             # 1-D: along its only axis
        out = (max(1, min(shape[0], TARGET_CHUNK_BYTES // size)),)
    else:                                                             # anything else: whole array, cap checked below
        out = tuple(shape)
    if math.prod(out) * size > MAX_CHUNK_BYTES:
        raise ValueError(f"chunk {out} of {dims} would be {math.prod(out) * size / 2**20:.0f} MiB (cap 50 MiB)")
    return tuple(out)
