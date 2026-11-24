"""The 10 automated QC tests. Each one is independent of the others and of any earlier flags."""
from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

from scats_common import MODEL
from scats_common.flags import MISSING
from .base import QCTest, TestResult, has_levels, model_variables, py

__all__ = ["ALL_TESTS", "MissingValue", "FormatConsistency", "GlobalRange", "RegionalRange", "Spike",
           "PositionOnLand", "PositionRange", "TimestampValid", "TimestampMonotonic", "DensityInversion"]


def _all(flags_like: np.ndarray) -> np.ndarray:
    return np.ones(flags_like.shape, dtype=bool)


class MissingValue(QCTest):
    test_id, category, severity = "missing_value", "missing", "hard"
    description = "Value is null, NaN or a fill value."

    def run(self, ds, cfg):
        res = TestResult()
        for name in model_variables(ds):
            vals = ds[name].values
            miss = np.isnan(vals)
            res.covered[name] = _all(vals)
            for idx in zip(*np.nonzero(miss)):
                res.findings.append(self.finding(cfg, name, idx, {"reason": "missing or fill value"}, flag=MISSING))
        for aux, arr in (("position", np.isnan(ds["latitude"].values) | np.isnan(ds["longitude"].values)
                          if "position_qc" in ds else None),
                         ("time", np.isnat(ds["time"].values) if "time_qc" in ds else None)):
            if arr is None:
                continue
            res.covered[aux] = _all(arr)
            for idx in zip(*np.nonzero(arr)):
                res.findings.append(self.finding(cfg, aux, idx, {"reason": "missing"}, flag=MISSING))
        return res


class FormatConsistency(QCTest):
    test_id, category, severity = "format_consistency", "format", "hard"
    description = "Field types, dimensions or units do not match the common data model."

    def run(self, ds, cfg):
        res = TestResult()
        for name in model_variables(ds):
            var, model = ds[name], MODEL.variables[name]
            res.covered[name] = _all(var.values)
            if str(var.attrs.get("units", "")) != model.units or f"{name}_qc" not in ds:
                for idx in np.ndindex(*var.shape):
                    res.findings.append(self.finding(cfg, name, idx, {
                        "reason": "units or flag variable do not match the data model",
                        "observed": str(var.attrs.get("units")), "expected": model.units}))
                continue
            bad = np.isinf(var.values)
            for idx in zip(*np.nonzero(bad)):
                res.findings.append(self.finding(cfg, name, idx, {"reason": "non-finite value",
                                                                  "observed": str(var.values[idx])}))
        return res


class GlobalRange(QCTest):
    test_id, category, severity = "global_range", "range", "hard"
    description = "Value outside the physically possible range."

    def run(self, ds, cfg):
        res = TestResult()
        for name in model_variables(ds):
            v, m = ds[name].values, MODEL.variables[name]
            with np.errstate(invalid="ignore"):
                out = (v < m.valid_min) | (v > m.valid_max)
            res.covered[name] = ~np.isnan(v)
            for idx in zip(*np.nonzero(out)):
                res.findings.append(self.finding(cfg, name, idx, {
                    "observed": py(v[idx]), "valid_min": m.valid_min, "valid_max": m.valid_max}))
        return res


class RegionalRange(QCTest):
    test_id, category, severity = "regional_range", "range", "soft"
    description = "Value outside the expected range for the region."

    def run(self, ds, cfg):
        res = TestResult()
        for name, spec in cfg["regional_range"].items():
            if name not in ds:
                continue
            v = ds[name].values
            with np.errstate(invalid="ignore"):
                out = (v < spec["min"]) | (v > spec["max"])
            res.covered[name] = ~np.isnan(v)
            for idx in zip(*np.nonzero(out)):
                res.findings.append(self.finding(cfg, name, idx, {
                    "observed": py(v[idx]), "min": spec["min"], "max": spec["max"], "region": cfg["region"]["name"]}))
        return res


def _spike_value(v: np.ndarray, axis: int) -> np.ndarray:
    """Argo test 9 quantity for the interior points along `axis`; NaN where a neighbour is missing."""
    v = np.moveaxis(v, axis, -1)
    out = np.full(v.shape, np.nan)
    v1, v2, v3 = v[..., :-2], v[..., 1:-1], v[..., 2:]
    out[..., 1:-1] = np.abs(v2 - (v1 + v3) / 2.0) - np.abs((v3 - v1) / 2.0)
    return np.moveaxis(out, -1, axis)


class Spike(QCTest):
    test_id, category, severity = "spike", "instrument", "soft"
    description = "Value departs sharply from its neighbours."

    def run(self, ds, cfg):
        res = TestResult()
        if has_levels(ds):
            self._profiles(ds, cfg, res)
        elif "record" in ds.dims:
            self._timeseries(ds, cfg, res)
        return res                                    # grids: no spike test in this phase

    def _profiles(self, ds, cfg, res):
        spec = cfg["spike"]["profile"]
        pres = ds["pressure"].values
        for name, thr in spec["variables"].items():
            if name not in ds:
                continue
            v = ds[name].values
            with np.errstate(invalid="ignore"):
                s = _spike_value(v, axis=1)
            limit = np.where(pres < spec["pressure_split_dbar"], thr["shallow"], thr["deep"])
            res.covered[name] = ~np.isnan(s)
            with np.errstate(invalid="ignore"):
                hit = s > limit
            for idx in zip(*np.nonzero(hit)):
                res.findings.append(self.finding(cfg, name, idx, {
                    "observed": py(v[idx]), "spike_value": py(s[idx]), "threshold": py(limit[idx]),
                    "pressure_dbar": py(pres[idx])}))

    def _timeseries(self, ds, cfg, res):
        for name, thr in cfg["spike"]["timeseries"].items():
            if name not in ds:
                continue
            v = ds[name].values
            with np.errstate(invalid="ignore"):
                s = _spike_value(v, axis=0)
                hit = s > thr["threshold"]
            res.covered[name] = ~np.isnan(s)
            for idx in zip(*np.nonzero(hit)):
                res.findings.append(self.finding(cfg, name, idx, {
                    "observed": py(v[idx]), "spike_value": py(s[idx]), "threshold": thr["threshold"]}))


class PositionRange(QCTest):
    test_id, category, severity = "position_range", "position", "hard"
    description = "Latitude/longitude outside valid limits."

    def run(self, ds, cfg):
        res = TestResult()
        if "position_qc" not in ds:
            return res
        lat, lon = ds["latitude"].values, ds["longitude"].values
        present = ~(np.isnan(lat) | np.isnan(lon))
        with np.errstate(invalid="ignore"):
            out = (np.abs(lat) > 90.0) | (np.abs(lon) > 180.0)
        res.covered["position"] = present
        for idx in zip(*np.nonzero(out & present)):
            res.findings.append(self.finding(cfg, "position", idx, {
                "observed_latitude": py(lat[idx]), "observed_longitude": py(lon[idx]),
                "valid": "latitude -90..90, longitude -180..180"}))
        return res


class PositionOnLand(QCTest):
    test_id, category, severity = "position_on_land", "position", "hard"
    description = "Latitude/longitude falls on land."

    def run(self, ds, cfg):
        from global_land_mask import globe
        res = TestResult()
        if "position_qc" not in ds:
            return res
        lat, lon = ds["latitude"].values, ds["longitude"].values
        with np.errstate(invalid="ignore"):
            usable = ~(np.isnan(lat) | np.isnan(lon)) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
        land = np.zeros(lat.shape, dtype=bool)
        if usable.any():
            land[usable] = globe.is_land(lat[usable], lon[usable])
        res.covered["position"] = usable
        for idx in zip(*np.nonzero(land)):
            res.findings.append(self.finding(cfg, "position", idx, {
                "observed_latitude": py(lat[idx]), "observed_longitude": py(lon[idx]),
                "method": "global_land_mask, about 1 km resolution"}))
        return res


class TimestampValid(QCTest):
    test_id, category, severity = "timestamp_valid", "timestamp", "hard"
    description = "Timestamp unparseable or outside plausible window."

    def run(self, ds, cfg):
        res = TestResult()
        if "time_qc" not in ds:
            return res
        t = ds["time"].values.astype("datetime64[ns]")
        spec = cfg["timestamp_valid"]
        earliest = np.datetime64(spec["earliest"], "ns")
        latest = (pd.Timestamp.now("UTC").tz_localize(None) + pd.Timedelta(days=spec["future_days"])).to_datetime64()
        ok = ~np.isnat(t)
        res.covered["time"] = ok
        out = ok & ((t < earliest) | (t > latest))
        for idx in zip(*np.nonzero(out)):
            res.findings.append(self.finding(cfg, "time", idx, {
                "observed": str(t[idx]), "earliest": spec["earliest"], "latest_allowed": str(latest)[:10]}))
        return res


class TimestampMonotonic(QCTest):
    test_id, category, severity = "timestamp_monotonic", "timestamp", "hard"
    description = "Timestamps duplicated or not increasing."

    def run(self, ds, cfg):
        res = TestResult()
        if "time_qc" not in ds:
            return res
        t = ds["time"].values.astype("datetime64[ns]")
        if t.ndim != 1:
            return res
        groups = ds["platform_group"].values.astype(str) if "platform_group" in ds else np.array([""] * t.size)
        order = ds["source_record"].values if "source_record" in ds else np.arange(t.size)
        covered = np.zeros(t.size, dtype=bool)
        for g in dict.fromkeys(groups.tolist()):
            members = np.flatnonzero(groups == g)
            members = members[np.argsort(order[members], kind="stable")]
            members = members[~np.isnat(t[members])]
            for prev, cur in zip(members[:-1], members[1:]):
                covered[cur] = True
                if t[cur] <= t[prev]:
                    res.findings.append(self.finding(cfg, "time", (cur,), {
                        "kind": "duplicate" if t[cur] == t[prev] else "decreasing",
                        "observed": str(t[cur]), "previous": str(t[prev]), "group": g}))
        res.covered["time"] = covered
        return res


class DensityInversion(QCTest):
    test_id, category, severity = "density_inversion", "cross_variable", "soft"
    description = "Density decreases with depth beyond tolerance."

    def run(self, ds, cfg):
        import gsw
        res = TestResult()
        if not has_levels(ds) or "temperature" not in ds or "salinity" not in ds:
            return res
        tol = cfg["density_inversion"]["tolerance_kg_m3"]
        T, S, P = ds["temperature"].values, ds["salinity"].values, ds["pressure"].values
        lat = np.clip(np.nan_to_num(ds["latitude"].values, nan=0.0), -90, 90)[:, None]
        lon = np.nan_to_num(ds["longitude"].values, nan=0.0)[:, None]
        with np.errstate(invalid="ignore"):
            pm = (P[:, :-1] + P[:, 1:]) / 2.0                     # reference every pair at its mid pressure
            lat2, lon2 = np.broadcast_to(lat, pm.shape), np.broadcast_to(lon, pm.shape)
            def rho(s, t):
                sa = gsw.SA_from_SP(s, pm, lon2, lat2)
                return gsw.rho(sa, gsw.CT_from_t(sa, t, pm), pm)
            drho = rho(S[:, :-1], T[:, :-1]) - rho(S[:, 1:], T[:, 1:])    # upper minus lower: should be <= 0
        valid = ~np.isnan(drho)
        cov = np.zeros(T.shape, dtype=bool)
        cov[:, :-1] |= valid
        cov[:, 1:] |= valid
        res.covered["temperature"] = cov
        res.covered["salinity"] = cov
        with np.errstate(invalid="ignore"):
            hit = drho > tol
        for i, k in zip(*np.nonzero(hit)):
            detail = {"drho_kg_m3": float(drho[i, k]), "tolerance": tol, "levels": [int(k), int(k) + 1],
                      "note": "cannot tell which level of the pair is wrong, so both are flagged"}
            for lvl in (k, k + 1):
                for name in ("temperature", "salinity"):
                    res.findings.append(self.finding(cfg, name, (i, lvl), dict(detail, observed=py(ds[name].values[i, lvl]))))
        return res


ALL_TESTS = [MissingValue, FormatConsistency, GlobalRange, RegionalRange, Spike, PositionOnLand, PositionRange,
             TimestampValid, TimestampMonotonic, DensityInversion]
