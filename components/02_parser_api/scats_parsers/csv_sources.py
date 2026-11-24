"""CSV sources: ship CTD casts (long form profiles) and buoy time series."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr

from scats_common import units as U
from .base import (Parser, ParseError, add_data_variable, aux_variables, convert_units, csv_columns,
                   depth_to_pressure, initial_flags, numeric_column, provenance_defaults, read_csv_header,
                   read_csv_table, register, set_dataset_attrs, sha256_of)


def _fill_values(m, meta) -> list[float]:
    fills = [float(x) for x in m.get("fill_values", [])]
    if "fill_value" in meta:
        try:
            fills.append(float(meta["fill_value"]))
        except ValueError:
            pass
    return fills


def _require(df, columns, path):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ParseError(f"'{path.name}' is missing required column(s): {', '.join(missing)}")


def _sniff(path: Path, needed: list[str]) -> bool:
    return Path(path).suffix.lower() == ".csv" and set(needed) <= set(csv_columns(path))


@register
class ShipCtdCsvParser(Parser):
    name = "ship_ctd_csv"
    mapping_file = "ship_ctd_csv.yaml"

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        return _sniff(path, cls.mapping()["detect"]["columns"])

    def parse(self, path: Path) -> xr.Dataset:
        m, path = self.mapping(), Path(path)
        meta, header = read_csv_header(path)
        df, sha = read_csv_table(path), sha256_of(path)
        c = m["columns"]
        _require(df, list(c.values()), path)
        fills = _fill_values(m, meta)

        casts = df[c["cast"]].to_numpy()
        order = list(dict.fromkeys(casts.tolist()))          # first-appearance order
        rows = {cid: np.flatnonzero(casts == cid) for cid in order}
        n_prof, n_lev = len(order), max(len(r) for r in rows.values())

        lat_all = numeric_column(df[c["latitude"]], fills)
        lon_all = U.lon_to_pm180(numeric_column(df[c["longitude"]], fills))
        time_all = U.parse_iso_utc(df[c["time"]].tolist())
        depth_all = numeric_column(df[c["depth"]], fills)
        first = np.array([rows[cid][0] for cid in order])
        lat, lon, times = lat_all[first], lon_all[first], time_all[first]

        def to_grid(values):
            grid = np.full((n_prof, n_lev), np.nan)
            for i, cid in enumerate(order):
                grid[i, :len(rows[cid])] = values[rows[cid]]
            return grid

        depth = to_grid(depth_all)
        pressure = depth_to_pressure(depth, lat)
        data_vars: dict = {}
        mapped = set(c.values())
        for src, spec in m["variables"].items():
            if src not in df.columns:
                continue
            mapped.add(src)
            values = convert_units(to_grid(numeric_column(df[src], fills)), spec["source_units"], spec["name"], src)
            add_data_variable(data_vars, spec["name"], ("profile", "level"), values, initial_flags(values), src,
                              spec["source_units"], "time latitude longitude depth")
        if not data_vars:
            raise ParseError(f"'{path.name}' has no mapped data column")

        group = meta.get(m.get("group_header_key", ""), path.stem)
        data_vars.update(aux_variables("profile", time=times, lat=lat, lon=lon, platform_id=order,
                                       platform_group=[group] * n_prof, depth=depth, pressure=pressure))
        ds = xr.Dataset(data_vars)
        synthetic = "SYNTHETIC" in header.upper()
        institution, license_ = provenance_defaults(synthetic, meta.get("institution"), meta.get("license"))
        return set_dataset_attrs(
            ds, mapping=m, parser_name=self.name, path=path, sha=sha, is_synthetic=synthetic, title=path.stem,
            institution=institution, license_=license_, source=meta.get("source", "unknown"), platform_id=group,
            time_values=times, lat_values=lat, lon_values=lon, depth_values=depth,
            extra={"ignored_columns": ",".join(sorted(set(df.columns) - mapped)) or "none"})


@register
class BuoyCsvParser(Parser):
    name = "buoy_csv"
    mapping_file = "buoy_csv.yaml"

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        return _sniff(path, cls.mapping()["detect"]["columns"])

    def parse(self, path: Path) -> xr.Dataset:
        m, path = self.mapping(), Path(path)
        meta, header = read_csv_header(path)
        df, sha = read_csv_table(path), sha256_of(path)
        c = m["columns"]
        _require(df, list(c.values()), path)
        fills = _fill_values(m, meta)

        lat = numeric_column(df[c["latitude"]], fills)
        lon = U.lon_to_pm180(numeric_column(df[c["longitude"]], fills))
        times = U.parse_iso_utc(df[c["time"]].tolist())
        data_vars: dict = {}
        mapped = set(c.values())
        for src, spec in m["variables"].items():
            if src not in df.columns:
                continue
            mapped.add(src)
            values = convert_units(numeric_column(df[src], fills), spec["source_units"], spec["name"], src)
            add_data_variable(data_vars, spec["name"], ("record",), values, initial_flags(values), src,
                              spec["source_units"], "time latitude longitude")
        if not data_vars:
            raise ParseError(f"'{path.name}' has no mapped data column")

        platform = meta.get(m.get("group_header_key", ""), path.stem)
        n = len(df)
        data_vars.update(aux_variables("record", time=times, lat=lat, lon=lon, platform_id=[platform] * n,
                                       platform_group=[platform] * n))
        ds = xr.Dataset(data_vars)
        synthetic = "SYNTHETIC" in header.upper()
        institution, license_ = provenance_defaults(synthetic, meta.get("institution"), meta.get("license"))
        return set_dataset_attrs(
            ds, mapping=m, parser_name=self.name, path=path, sha=sha, is_synthetic=synthetic, title=path.stem,
            institution=institution, license_=license_, source=meta.get("source", "unknown"), platform_id=platform,
            time_values=times, lat_values=lat, lon_values=lon,
            extra={"ignored_columns": ",".join(sorted(set(df.columns) - mapped)) or "none"})
