"""Parser framework: the Parser base class, the registry, and helpers shared by all parsers.

Adding a format = one Parser subclass + one YAML mapping + @register. See docs/phase2_design.md.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from abc import ABC, abstractmethod
from pathlib import Path

import gsw
import numpy as np
import pandas as pd
import xarray as xr
import yaml

from scats_common import MODEL
from scats_common import units as U
from scats_common.flags import FLAG_MEANINGS, MISSING

__version__ = "0.1.0"
MAPPING_DIR = Path(__file__).parent / "mappings"
NETCDF_SUFFIXES = (".nc", ".nc4", ".cdf")


class ParseError(Exception):
    """A file could not be read or does not look like what its parser expects. Never a crash."""


# ----------------------------------------------------------------------------- mapping
class SourceMapping:
    """A YAML mapping file. `standard_name_of` is what the scoring harness uses as well."""

    def __init__(self, filename: str):
        self.filename = filename
        self.raw: dict = yaml.safe_load((MAPPING_DIR / filename).read_text())

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def standard_name_of(self, source_name: str) -> str | None:
        """Common name ('temperature'), or the pseudo variable 'position' / 'time', or None."""
        spec = self.raw.get("variables", {}).get(source_name)
        if spec:
            return spec["name"]
        return self.raw.get("coordinates", {}).get(source_name)


# ---------------------------------------------------------------------------- registry
_REGISTRY: list[type["Parser"]] = []


class Parser(ABC):
    name: str = ""
    mapping_file: str = ""
    _mapping: SourceMapping | None = None

    @classmethod
    def mapping(cls) -> SourceMapping:
        if cls.__dict__.get("_mapping") is None:
            cls._mapping = SourceMapping(cls.mapping_file)
        return cls._mapping

    @classmethod
    @abstractmethod
    def can_parse(cls, path: Path) -> bool:
        """Cheap sniff: extension plus content. Must never raise."""

    @abstractmethod
    def parse(self, path: Path) -> xr.Dataset:
        """Return the common data model. Raise ParseError (never anything else) on a bad file."""


def register(cls):
    _REGISTRY.append(cls)
    return cls


def parsers() -> list[type[Parser]]:
    return list(_REGISTRY)


def detect(path) -> type[Parser] | None:
    path = Path(path)
    for cls in _REGISTRY:
        try:
            if cls.can_parse(path):
                return cls
        except Exception:  # noqa: BLE001 - a broken sniff must not stop the others
            continue
    return None


def parse_file(path) -> xr.Dataset:
    path = Path(path)
    if not path.is_file():
        raise ParseError(f"file not found: {path}")
    cls = detect(path)
    if cls is None:
        raise ParseError(f"no parser recognises '{path.name}'. Known formats: "
                         + ", ".join(p.name for p in _REGISTRY))
    try:
        return cls().parse(path)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert surprises into a clear error
        raise ParseError(f"{cls.name} failed on '{path.name}': {type(exc).__name__}: {exc}") from exc


# -------------------------------------------------------------------------- file helpers
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_netcdf(path: Path) -> xr.Dataset:
    try:
        return xr.open_dataset(path).load()
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"cannot read '{Path(path).name}' as NetCDF: {exc}") from exc


def netcdf_has(path: Path, variables=(), variable: str | None = None) -> bool:
    if Path(path).suffix.lower() not in NETCDF_SUFFIXES:
        return False
    try:
        with xr.open_dataset(path) as d:
            names = set(d.variables)
            return set(variables) <= names and (variable is None or variable in names)
    except Exception:  # noqa: BLE001
        return False


def read_csv_header(path: Path) -> tuple[dict[str, str], str]:
    """'# key: value' lines at the top of a CSV. Returns (key->value, raw header text)."""
    meta, lines = {}, []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            lines.append(line.rstrip("\n"))
            m = re.match(r"#\s*([\w .-]+?)\s*:\s*(.*)$", line.rstrip("\n"))
            if m:
                meta[m.group(1).strip().lower()] = m.group(2).strip()
    return meta, "\n".join(lines)


_SYNTH = {"true": "true", "yes": "true", "1": "true", "false": "false", "no": "false", "0": "false"}


def synthetic_status(value, where: str) -> str:
    """Explicit declaration only. Missing -> 'unknown'. A malformed declaration is an error, not a guess."""
    if value is None or str(value).strip() == "":
        return "unknown"
    try:
        return _SYNTH[str(value).strip().lower()]
    except KeyError:
        raise ParseError(f"{where}: 'synthetic' must be true or false, got '{value}'") from None


def declared_units(meta: dict[str, str], path: Path) -> dict[str, str]:
    """'# units: col=unit, col2=unit2' header line -> {col: unit}."""
    raw = meta.get("units", "")
    out = {}
    for part in (p for p in raw.split(",") if p.strip()):
        col, sep, unit = part.partition("=")
        if not sep or not col.strip() or not unit.strip() or "=" in unit:
            raise ParseError(f"'{Path(path).name}': cannot read units declaration '{part.strip()}' (use 'col=unit' pairs separated by commas)")
        out[col.strip()] = unit.strip()
    return out


def csv_columns(path: Path) -> list[str]:
    try:
        return list(pd.read_csv(path, comment="#", nrows=0).columns)
    except Exception:  # noqa: BLE001
        return []


def read_csv_table(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path, comment="#", dtype=str, keep_default_na=False)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"cannot read '{Path(path).name}' as CSV: {exc}") from exc
    if df.empty:
        raise ParseError(f"'{Path(path).name}' has no data rows")
    return df


def numeric_column(series: pd.Series, fill_values) -> np.ndarray:
    """Strings -> float. Empty cells, unparseable text and fill values become NaN."""
    values = np.array(pd.to_numeric(series, errors="coerce"), dtype="float64")  # writable copy
    for fv in fill_values:
        values[values == float(fv)] = np.nan
    return values


# ------------------------------------------------------------------------------ geometry
def clip_lat(lat) -> np.ndarray:
    """Latitude usable for pressure<->depth only: missing -> 0, out of range clipped."""
    return np.clip(np.nan_to_num(np.asarray(lat, dtype="float64"), nan=0.0), -90.0, 90.0)


def pressure_to_depth(pressure, lat) -> np.ndarray:
    lat = clip_lat(lat)
    p = np.asarray(pressure, dtype="float64")
    return -gsw.z_from_p(p, lat[:, None] if p.ndim == 2 else lat)


def depth_to_pressure(depth, lat) -> np.ndarray:
    lat = clip_lat(lat)
    z = np.asarray(depth, dtype="float64")
    return gsw.p_from_z(-z, lat[:, None] if z.ndim == 2 else lat)


# ------------------------------------------------------------------------------- flags
def initial_flags(values: np.ndarray, provider: np.ndarray | None = None) -> np.ndarray:
    """Provider flags where given (else 0), then 9 wherever the value is missing."""
    flags = np.zeros(values.shape, dtype=np.int8) if provider is None else provider.astype(np.int8).copy()
    flags[np.isnan(values)] = MISSING
    return flags


def flag_attrs() -> dict:
    return {"long_name": "quality flag", "flag_values": np.array(sorted(FLAG_MEANINGS), dtype=np.int8),
            "flag_meanings": " ".join(FLAG_MEANINGS[k] for k in sorted(FLAG_MEANINGS))}


def variable_attrs(name: str, source_name: str, source_units: str, coordinates: str) -> dict:
    spec = MODEL.variable(name)
    return {"standard_name": spec.standard_name, "long_name": spec.long_name, "units": spec.units,
            "source_name": source_name, "source_units": source_units, "valid_min": spec.valid_min,
            "valid_max": spec.valid_max, "ancillary_variables": f"{name}_qc", "coordinates": coordinates}


def add_data_variable(data_vars: dict, name: str, dims, values, flags, source_name: str,
                      source_units: str, coordinates: str) -> None:
    """Add <name> and <name>_qc to a data_vars dict, with the attributes the data model requires."""
    var = xr.Variable(dims, np.asarray(values, dtype="float64"), variable_attrs(name, source_name, source_units, coordinates))
    var.encoding["_FillValue"] = np.nan
    data_vars[name] = var
    data_vars[f"{name}_qc"] = xr.Variable(dims, np.asarray(flags, dtype=np.int8), flag_attrs())


def convert_units(values: np.ndarray, source_units: str, name: str, source_name: str) -> np.ndarray:
    try:
        return U.convert(values, source_units, MODEL.variable(name).units)
    except U.UnitError as exc:
        raise ParseError(f"{source_name}: {exc}") from exc


# ----------------------------------------------------------------------------- metadata
def _iso(value) -> str:
    return "" if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_dataset_attrs(ds: xr.Dataset, *, mapping: SourceMapping, parser_name: str, path: Path, sha: str,
                      title: str, institution: str, license_: str, platform_id: str, is_synthetic: str,
                      source: str, time_values=None, lat_values=None, lon_values=None,
                      depth_values=None, instrument: str | None = None, extra: dict | None = None) -> xr.Dataset:
    """Fill every dataset-level attribute that docs/data_model.md section 5 requires."""
    kind = mapping["dataset_type"]
    t = np.asarray(time_values) if time_values is not None else np.array([], dtype="datetime64[ns]")
    t = t[~pd.isna(t)] if t.size else t
    lat = np.asarray(lat_values, dtype="float64") if lat_values is not None else np.array([])
    lon = np.asarray(lon_values, dtype="float64") if lon_values is not None else np.array([])
    lat, lon = lat[np.isfinite(lat) & (np.abs(lat) <= 90)], lon[np.isfinite(lon) & (np.abs(lon) <= 180)]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n_obs = int(ds.sizes.get("profile", ds.sizes.get("record", ds.sizes.get("time", 1))))
    unit = {"profile": "profiles", "timeseries": "records", "grid": "time slices", "bathymetry": "grid"}[kind]
    attrs = {
        "title": title,
        "summary": f"{n_obs} {unit} standardised from '{path.name}' ({mapping['source_type']}).",
        "Conventions": MODEL.conventions,
        "data_model_version": MODEL.version,
        "source": source,
        "institution": institution,
        "platform_type": mapping["platform_type"],
        "platform_id": platform_id,
        "instrument": instrument or mapping.get("instrument", ""),
        "data_mode": "synthetic" if is_synthetic == "true" else mapping["data_mode_default"],
        "is_synthetic": is_synthetic,
        "license": license_,
        "time_coverage_start": _iso(t.min()) if t.size else "not applicable",
        "time_coverage_end": _iso(t.max()) if t.size else "not applicable",
        "geospatial_lat_min": float(lat.min()) if lat.size else float("nan"),
        "geospatial_lat_max": float(lat.max()) if lat.size else float("nan"),
        "geospatial_lon_min": float(lon.min()) if lon.size else float("nan"),
        "geospatial_lon_max": float(lon.max()) if lon.size else float("nan"),
        "processing_level": "standardised",
        "dataset_type": kind,
        "original_file_name": path.name,
        "original_checksum_sha256": sha,
        "date_created": now,
        "history": f"{now} parsed by scats_parsers {__version__} ({parser_name}) from '{path.name}'",
        "parser": parser_name,
    }
    if depth_values is not None:
        d = np.asarray(depth_values, dtype="float64")
        d = d[np.isfinite(d)]
        if d.size:
            attrs["geospatial_vertical_min"], attrs["geospatial_vertical_max"] = float(d.min()), float(d.max())
    attrs.update(extra or {})
    ds.attrs.update(attrs)
    return ds


def provenance_defaults(is_synthetic: str, institution: str | None, license_: str | None) -> tuple[str, str]:
    if is_synthetic == "true":
        return institution or "none (synthetic)", license_ or "synthetic test data, no restrictions"
    return institution or "unknown (not stated in file)", license_ or "unknown (not stated in file)"


# ---------------------------------------------------------------- auxiliary variables
TIME_ENCODING = {"units": "seconds since 1970-01-01 00:00:00", "dtype": "float64", "_FillValue": np.nan}


def aux_variables(obs_dim: str, *, time, lat, lon, platform_id, platform_group, depth=None, pressure=None,
                  n_levels=None) -> dict:
    """time, position, platform, depth/pressure and the position_qc / time_qc flags of an observation set."""
    time = np.asarray(time, dtype="datetime64[ns]")
    lat, lon = np.asarray(lat, dtype="float64"), np.asarray(lon, dtype="float64")
    n = lat.shape[0]
    t = xr.Variable(obs_dim, time, {"standard_name": "time", "long_name": "observation time (UTC)"})
    t.encoding.update(TIME_ENCODING)
    out = {
        "time": t,
        "latitude": xr.Variable(obs_dim, lat, {"standard_name": "latitude", "units": "degrees_north",
                                               "long_name": "latitude (WGS84)"}),
        "longitude": xr.Variable(obs_dim, lon, {"standard_name": "longitude", "units": "degrees_east",
                                                "long_name": "longitude (WGS84), -180 to 180"}),
        "platform_id": xr.Variable(obs_dim, np.asarray(platform_id, dtype=object), {"long_name": "platform / cast id"}),
        "platform_group": xr.Variable(obs_dim, np.asarray(platform_group, dtype=object),
                                      {"long_name": "float / cruise / buoy used to order observations in time"}),
        "source_record": xr.Variable(obs_dim, np.arange(n, dtype="int64"),
                                     {"long_name": "0-based record index in the source file"}),
        "position_qc": xr.Variable(obs_dim, np.where(np.isnan(lat) | np.isnan(lon), MISSING, 0).astype(np.int8), flag_attrs()),
        "time_qc": xr.Variable(obs_dim, np.where(np.isnat(time), MISSING, 0).astype(np.int8), flag_attrs()),
    }
    if depth is not None:
        out["depth"] = xr.Variable((obs_dim, "level"), np.asarray(depth, dtype="float64"),
                                   {"standard_name": "depth", "units": "m", "positive": "down"})
        out["pressure"] = xr.Variable((obs_dim, "level"), np.asarray(pressure, dtype="float64"),
                                      {"standard_name": "sea_water_pressure", "units": "dbar"})
        d = np.asarray(depth, dtype="float64")
        if n_levels is None:
            finite = np.isfinite(d)
            n_levels = np.where(finite.any(axis=1), d.shape[1] - np.argmax(finite[:, ::-1], axis=1), 0)
        out["n_levels"] = xr.Variable(obs_dim, np.asarray(n_levels, dtype="int64"),
                                      {"long_name": "number of valid levels in the profile"})
    return out
