"""What goes into PostgreSQL, decided from the flag arrays alone.

Used by the ingest service to write rows and by the consistency check to recompute them from the
staging file, so the two can never disagree about the rule.

Rule (docs/phase2_design.md section 3):
  * profiles and time series: one row per cell whose flag is 2, 3, 4, 5, 8 or 9. Padding levels of ragged
    profiles (level >= n_levels) are not observations and get no row.
  * grids: one summary row per time slice and variable (level_index -1) when any cell is 3 or 4.
    Structurally missing cells (land, no data) stay in staging only.
  * position and time: one row per observation with a persisted flag (variable 'position' / 'time').
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import xarray as xr

from scats_common import MODEL
from scats_common import flags as F

FLAG_VAR_SUFFIX = "_qc"


@dataclass(frozen=True)
class Row:
    obs: int                  # observation index = source_record
    variable: str
    level: int
    flag: int
    n3: int = 0               # grid summaries: number of cells with flag 3 / 4
    n4: int = 0

    @property
    def key(self) -> tuple:
        return (self.obs, self.variable, self.level)


def kind_of(ds: xr.Dataset) -> str:
    return ds.attrs["dataset_type"]


def n_observations(ds: xr.Dataset) -> int:
    k = kind_of(ds)
    return {"profile": ds.sizes.get("profile"), "timeseries": ds.sizes.get("record"),
            "grid": ds.sizes.get("time"), "bathymetry": 1}[k]


def data_variables(ds: xr.Dataset) -> list[str]:
    return [v for v in MODEL.variables if v in ds.data_vars]


def _valid_level_mask(ds: xr.Dataset) -> np.ndarray:
    n_lev = ds["n_levels"].values
    return np.arange(ds.sizes["level"])[None, :] < n_lev[:, None]


def expected_rows(ds: xr.Dataset) -> list[Row]:
    kind, rows = kind_of(ds), []
    persisted = np.array(F.PERSISTED)
    if kind in ("profile", "timeseries"):
        valid = _valid_level_mask(ds) if kind == "profile" else None
        for var in data_variables(ds):
            q = ds[f"{var}{FLAG_VAR_SUFFIX}"].values
            hit = np.isin(q, persisted)
            if valid is not None:
                hit &= valid
            for idx in zip(*np.nonzero(hit)):
                rows.append(Row(int(idx[0]), var, int(idx[1]) if len(idx) > 1 else -1, int(q[idx])))
        for aux in ("position", "time"):
            name = f"{aux}_qc"
            if name in ds:
                q = ds[name].values
                for i in np.nonzero(np.isin(q, persisted))[0]:
                    rows.append(Row(int(i), aux, -1, int(q[i])))
    else:                                             # grids
        for var in data_variables(ds):
            q = ds[f"{var}{FLAG_VAR_SUFFIX}"].values
            q = q[None, ...] if kind == "bathymetry" else q
            n3 = (q == F.PROBABLY_BAD).reshape(q.shape[0], -1).sum(axis=1)
            n4 = (q == F.BAD).reshape(q.shape[0], -1).sum(axis=1)
            for t in np.nonzero((n3 + n4) > 0)[0]:
                rows.append(Row(int(t), var, -1, F.BAD if n4[t] else F.PROBABLY_BAD, int(n3[t]), int(n4[t])))
        if "time_qc" in ds:
            q = ds["time_qc"].values
            for i in np.nonzero(np.isin(q, persisted))[0]:
                rows.append(Row(int(i), "time", -1, int(q[i])))
    return rows


def overall_flags(ds: xr.Dataset) -> np.ndarray:
    """Worst flag per observation. Grids ignore flag 9 (structural), points and profiles do not."""
    kind, n = kind_of(ds), n_observations(ds)
    out = np.zeros(n, dtype=np.int8)
    valid = _valid_level_mask(ds) if kind == "profile" else None
    for i in range(n):
        parts = []
        for var in data_variables(ds):
            q = ds[f"{var}{FLAG_VAR_SUFFIX}"].values
            if kind == "profile":
                parts.append(q[i][valid[i]])
            elif kind == "timeseries":
                parts.append(q[i:i + 1])
            else:
                cells = q[i] if kind == "grid" else q
                parts.append(cells[cells != F.MISSING])
        for aux in ("position_qc", "time_qc"):
            if aux in ds:
                parts.append(ds[aux].values[i:i + 1])
        out[i] = F.worst(np.concatenate([np.ravel(p) for p in parts])) if parts else 0
    return out


def cleared_flag(ds: xr.Dataset, obs: int, variable: str, level: int) -> int:
    """The flag a cell has NOW, for a cell that an earlier QC run had flagged and this run does not.

    Used to write the "cleared" row (docs/phase3_design.md section 4). Grid summary rows have no single cell, so a
    summary that no longer has any flag 3/4 cell is cleared to 1 (good) and the time flag is read as it is.
    """
    kind = kind_of(ds)
    if variable in ("position", "time"):
        name = f"{variable}_qc"
        return int(ds[name].values[obs]) if name in ds else int(F.GOOD)
    if kind in ("profile", "timeseries"):
        q = ds[f"{variable}{FLAG_VAR_SUFFIX}"].values
        return int(q[obs, level]) if kind == "profile" else int(q[obs])
    return int(F.GOOD)
