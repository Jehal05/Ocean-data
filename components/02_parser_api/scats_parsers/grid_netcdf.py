"""Gridded NetCDF sources: SST (time x lat x lon) and bathymetry (lat x lon)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr

from scats_common import units as U
from .base import (Parser, ParseError, add_data_variable, convert_units, flag_attrs, initial_flags, netcdf_has,
                   open_netcdf, provenance_defaults, register, set_dataset_attrs, sha256_of, synthetic_status)
from scats_common.flags import MISSING


def _grid_dataset(parser: "GridParser", path: Path, with_time: bool) -> xr.Dataset:
    m = parser.mapping()
    raw, sha = open_netcdf(path), sha256_of(path)
    (src, spec), = m["variables"].items()
    if src not in raw:
        raise ParseError(f"variable '{src}' not found in '{path.name}'")
    da, dims = raw[src], m["dims"]
    missing = [d for d in dims.values() if d not in da.dims]
    if missing:
        raise ParseError(f"'{src}' lacks expected dimension(s) {missing}; has {da.dims}")
    da = da.rename({v: k for k, v in dims.items()})
    da = da.transpose(*(["time"] if with_time else []), "latitude", "longitude")

    values = da.values.astype("float64")
    units = da.attrs.get("units") or spec["source_units"]
    assumed = not da.attrs.get("units")
    extra: dict = {}
    if spec.get("transform") == "elevation_to_depth":
        values = U.elevation_to_depth(values)
        land = values < 0                      # above sea level: no sea floor depth here
        extra["n_land_masked"] = int(land.sum())
        values = np.where(land, np.nan, values)
        units = spec["source_units"]
    values = convert_units(values, units, spec["name"], src)

    lat = raw[dims["latitude"]].values.astype("float64")
    lon = U.lon_to_pm180(raw[dims["longitude"]].values.astype("float64"))
    coords = {"latitude": ("latitude", lat, {"standard_name": "latitude", "units": "degrees_north"}),
              "longitude": ("longitude", lon, {"standard_name": "longitude", "units": "degrees_east"})}
    data_vars: dict = {}
    dim_names = tuple(da.dims)
    add_data_variable(data_vars, spec["name"], dim_names, values, initial_flags(values), src, str(units),
                      " ".join(dim_names))
    times = None
    if with_time:
        times = raw[dims["time"]].values.astype("datetime64[ns]")
        coords["time"] = ("time", times, {"standard_name": "time"})
        data_vars["time_qc"] = xr.Variable("time", np.where(np.isnat(times), MISSING, 0).astype(np.int8), flag_attrs())
    ds = xr.Dataset(data_vars, coords=coords)
    if with_time:
        ds["time"].encoding.update(units="seconds since 1970-01-01 00:00:00", dtype="float64", _FillValue=np.nan)

    synthetic = synthetic_status(raw.attrs.get("is_synthetic"), path.name)
    institution, license_ = provenance_defaults(synthetic, raw.attrs.get("institution"), raw.attrs.get("license"))
    extra["n_missing_cells"] = int(np.isnan(values).sum())
    extra["units_assumed_from_mapping"] = "true" if assumed else "false"
    return set_dataset_attrs(
        ds, mapping=m, parser_name=parser.name, path=path, sha=sha, is_synthetic=synthetic,
        title=str(raw.attrs.get("title", path.stem)), institution=institution, license_=license_,
        source=str(raw.attrs.get("source", "unknown")), platform_id="gridded product",
        time_values=times, lat_values=lat, lon_values=lon, extra=extra)


class GridParser(Parser):
    with_time = True

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        return netcdf_has(path, variable=cls.mapping()["detect"]["variable"])

    def parse(self, path: Path) -> xr.Dataset:
        return _grid_dataset(self, Path(path), self.with_time)


@register
class SstGridParser(GridParser):
    name = "sst_grid"
    mapping_file = "sst_grid.yaml"
    with_time = True


@register
class BathymetryGridParser(GridParser):
    name = "bathymetry_grid"
    mapping_file = "bathymetry_grid.yaml"
    with_time = False
