"""validate_dataset(): check an xarray Dataset against common data model v0.1.

Returns a list of human-readable violations (empty list = valid). It checks STRUCTURE and
METADATA only. Whether the values are plausible is the QC framework's job.
"""
from __future__ import annotations

import numpy as np
import xarray as xr

from .model import MODEL

# Variables allowed in addition to the standard data variables and their <name>_qc flags.
AUXILIARY = {"time", "latitude", "longitude", "depth", "pressure", "platform_id", "platform_group",
             "source_record", "n_levels", "position_qc", "time_qc"}

EXPECTED_DIMS = {
    "profile": {"profile", "level"},
    "timeseries": {"record"},
    "grid": {"time", "latitude", "longitude"},
    "bathymetry": {"latitude", "longitude"},
}


def _has_fill(var: xr.DataArray) -> bool:
    return "_FillValue" in var.encoding or "_FillValue" in var.attrs or "missing_value" in var.attrs


def validate_dataset(ds: xr.Dataset) -> list[str]:
    errors: list[str] = []

    # ---- dataset-level attributes
    for key in MODEL.required_dataset_attrs:
        value = ds.attrs.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"dataset attribute '{key}' is missing or empty")
    if "SCATS-" + MODEL.version not in str(ds.attrs.get("Conventions", "")):
        errors.append(f"Conventions must mention SCATS-{MODEL.version}")
    if str(ds.attrs.get("data_model_version", "")) != MODEL.version:
        errors.append(f"data_model_version must be '{MODEL.version}'")
    for attr in ("platform_type", "dataset_type", "data_mode", "processing_level"):
        value = ds.attrs.get(attr)
        if value is not None and value not in MODEL.vocabularies[attr]:
            errors.append(f"{attr} '{value}' is not in the controlled vocabulary")
    if str(ds.attrs.get("is_synthetic", "")) not in ("true", "false"):
        errors.append("is_synthetic must be the string 'true' or 'false'")

    # ---- structure
    dtype = ds.attrs.get("dataset_type")
    if dtype in EXPECTED_DIMS:
        missing = EXPECTED_DIMS[dtype] - set(ds.dims)
        if missing:
            errors.append(f"dataset_type '{dtype}' needs dimensions {sorted(missing)}")
    if dtype in ("profile", "timeseries"):
        for name in ("position_qc", "time_qc", "time", "latitude", "longitude", "source_record"):
            if name not in ds.variables:
                errors.append(f"variable '{name}' is required for dataset_type '{dtype}'")
    if dtype == "grid" and "time_qc" not in ds.variables:
        errors.append("variable 'time_qc' is required for dataset_type 'grid'")

    # ---- variables
    standard = [n for n in ds.data_vars if n in MODEL.variables]
    if not standard:
        errors.append("no standard data variable present")
    for name in ds.data_vars:
        if name in MODEL.variables or name.endswith("_qc") or name in AUXILIARY:
            continue
        errors.append(f"variable '{name}' is not part of the data model")

    for name in standard:
        var, spec, qc_name = ds[name], MODEL.variables[name], f"{name}_qc"
        for attr in MODEL.required_variable_attrs:
            if attr not in var.attrs or var.attrs[attr] in (None, ""):
                errors.append(f"{name}: attribute '{attr}' is missing")
        if not _has_fill(var):
            errors.append(f"{name}: _FillValue is missing")
        if var.attrs.get("units") != spec.units:
            errors.append(f"{name}: units '{var.attrs.get('units')}' must be '{spec.units}'")
        if var.attrs.get("standard_name") != spec.standard_name:
            errors.append(f"{name}: standard_name must be '{spec.standard_name}'")
        if "valid_min" in var.attrs and float(var.attrs["valid_min"]) != spec.valid_min:
            errors.append(f"{name}: valid_min must be {spec.valid_min}")
        if "valid_max" in var.attrs and float(var.attrs["valid_max"]) != spec.valid_max:
            errors.append(f"{name}: valid_max must be {spec.valid_max}")
        if var.attrs.get("ancillary_variables") != qc_name:
            errors.append(f"{name}: ancillary_variables must be '{qc_name}'")
        if qc_name not in ds.variables:
            errors.append(f"{name}: flag variable '{qc_name}' is missing")
        else:
            qc = ds[qc_name]
            if qc.dims != var.dims:
                errors.append(f"{qc_name}: dims {qc.dims} differ from {name} dims {var.dims}")
            if qc.dtype != np.int8:
                errors.append(f"{qc_name}: dtype must be int8, is {qc.dtype}")
            bad = set(np.unique(qc.values).tolist()) - set(MODEL.flag_scheme)
            if bad:
                errors.append(f"{qc_name}: values {sorted(bad)} are not in the flag scheme")

    for name in ("position_qc", "time_qc"):
        if name in ds.variables:
            bad = set(np.unique(ds[name].values).tolist()) - set(MODEL.flag_scheme)
            if bad:
                errors.append(f"{name}: values {sorted(bad)} are not in the flag scheme")
    return errors
