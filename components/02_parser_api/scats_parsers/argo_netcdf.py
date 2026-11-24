"""Argo-style profile NetCDF (N_PROF x N_LEVELS)."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import xarray as xr

from scats_common import units as U
from .base import (Parser, ParseError, add_data_variable, aux_variables, convert_units, initial_flags,
                   netcdf_has, open_netcdf, pressure_to_depth, provenance_defaults, register, set_dataset_attrs,
                   sha256_of, synthetic_status)


def _decode_text(arr) -> list[str]:
    return [x.decode("ascii", "replace").strip() if isinstance(x, (bytes, np.bytes_)) else str(x).strip()
            for x in np.asarray(arr).ravel()]


def provider_flags(qc_chars: np.ndarray, table: dict) -> np.ndarray:
    """Provider flag characters (bytes, one per cell) -> SCATS flags. Unknown or blank -> 0."""
    lut = np.zeros(256, dtype=np.int8)
    for ch, flag in table.items():
        lut[ord(str(ch)[0])] = int(flag)
    arr = np.asarray(qc_chars)
    if arr.dtype.kind == "S":
        codes = np.frombuffer(arr.astype("S1").tobytes(), dtype=np.uint8).reshape(arr.shape)
    else:
        codes = np.array([[ord((c or " ")[0]) & 0xFF for c in row] for row in arr.astype(str)], dtype=np.uint8)
    return lut[codes]


def _argo_times(juld: xr.DataArray) -> np.ndarray:
    if np.issubdtype(juld.dtype, np.datetime64):
        return juld.values.astype("datetime64[ns]")
    ref = re.search(r"days since (\S+)", str(juld.attrs.get("units", "")))
    return U.argo_juld_to_datetime(juld.values, ref.group(1) if ref else "1950-01-01")


@register
class ArgoNetCDFParser(Parser):
    name = "argo_netcdf"
    mapping_file = "argo_netcdf.yaml"

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        return netcdf_has(path, cls.mapping()["detect"]["variables"])

    def parse(self, path: Path) -> xr.Dataset:
        m, path = self.mapping(), Path(path)
        raw, sha = open_netcdf(path), sha256_of(path)
        pres_src = m["pressure"]["source"]
        if raw[pres_src].ndim != 2:
            raise ParseError(f"{pres_src} must be 2-D (profile x level), has dims {raw[pres_src].dims}")
        n_prof, n_lev = raw[pres_src].shape

        lat = raw["LATITUDE"].values.astype("float64")
        lon = U.lon_to_pm180(raw["LONGITUDE"].values.astype("float64"))
        times = _argo_times(raw["JULD"])
        platforms = _decode_text(raw[m["platform"]].values)
        pres_units = raw[pres_src].attrs.get("units") or m["pressure"]["source_units"]
        pressure = U.convert(raw[pres_src].values.astype("float64"), pres_units, "dbar")
        depth = pressure_to_depth(pressure, lat)

        data_vars: dict = {}
        coords_attr = "time latitude longitude depth"
        assumed: list[str] = []
        if not raw[pres_src].attrs.get("units"):
            assumed.append(pres_src)
        for src, spec in m["variables"].items():
            if src not in raw:
                continue
            if not raw[src].attrs.get("units"):
                assumed.append(src)
            units = raw[src].attrs.get("units") or spec["source_units"]
            values = convert_units(raw[src].values.astype("float64"), units, spec["name"], src)
            prov = None
            if spec.get("qc") in raw:
                prov = provider_flags(raw[spec["qc"]].values, m["provider_flags"])
            add_data_variable(data_vars, spec["name"], ("profile", "level"), values, initial_flags(values, prov),
                              src, str(units), coords_attr)
        if not [v for v in data_vars if not v.endswith("_qc")]:
            raise ParseError("no mapped data variable found in the file")

        data_vars.update(aux_variables("profile", time=times, lat=lat, lon=lon, platform_id=platforms,
                                       platform_group=platforms, depth=depth, pressure=pressure))
        ds = xr.Dataset(data_vars)

        synthetic = synthetic_status(raw.attrs.get("is_synthetic"), path.name)
        unique = sorted(set(platforms))
        institution, license_ = provenance_defaults(synthetic, raw.attrs.get("institution"), raw.attrs.get("license"))
        return set_dataset_attrs(
            ds, mapping=m, parser_name=self.name, path=path, sha=sha, is_synthetic=synthetic,
            title=str(raw.attrs.get("title", path.stem)), institution=institution, license_=license_,
            source=str(raw.attrs.get("source", "unknown")),
            platform_id=unique[0] if len(unique) == 1 else f"multiple ({len(unique)} platforms)",
            time_values=times, lat_values=lat, lon_values=lon, depth_values=depth,
            extra={"units_assumed_from_mapping": "true" if assumed else "false"})
