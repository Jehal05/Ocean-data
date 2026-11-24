"""Runs the QC tests over a parsed dataset and combines their flags (worst wins)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import xarray as xr

from scats_common import flags as F
from .base import Finding, QCTest
from .config import load_config
from .tests import ALL_TESTS


@dataclass
class QCReport:
    config_version: str
    tests_run: list[str]
    findings: list[Finding] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.test_id] = out.get(f.test_id, 0) + 1
        return out

    def worst_by_cell(self) -> dict[tuple, Finding]:
        """(variable, index) -> the finding that decided the final flag (worst flag, then first test)."""
        best: dict[tuple, Finding] = {}
        for f in self.findings:
            key = (f.variable, f.index)
            if key not in best or F.rank_of(f.flag) > F.rank_of(best[key].flag):
                best[key] = f
        return best


def flag_name(variable: str) -> str:
    return "position_qc" if variable == "position" else "time_qc" if variable == "time" else f"{variable}_qc"


def run_qc(ds: xr.Dataset, cfg: dict | None = None, tests: list[QCTest] | None = None,
           reference_time: str | None = None) -> QCReport:
    """Apply every test; update `<variable>_qc` arrays in place; return the findings.

    Safe to run twice: findings do not depend on earlier flags, and flags only combine upward.
    """
    cfg = dict(cfg or load_config())
    cfg["_reference_time"] = reference_time
    tests = tests or [t() for t in ALL_TESTS]
    report = QCReport(config_version=cfg["version"], tests_run=[t.test_id for t in tests])
    covered: dict[str, np.ndarray] = {}
    for test in tests:
        result = test.run(ds, cfg)
        report.findings.extend(result.findings)
        for var, mask in result.covered.items():
            covered[var] = covered.get(var, np.zeros(mask.shape, dtype=bool)) | mask
    for var in {f.variable for f in report.findings}:
        arr = ds[flag_name(var)].values.copy()
        for f in (x for x in report.findings if x.variable == var):
            arr[f.index] = F.combine(arr[f.index], f.flag)
        ds[flag_name(var)].values[...] = arr
    for var, mask in covered.items():                         # passed every test applied -> good
        name = flag_name(var)
        if name in ds:
            arr = ds[name].values
            arr[mask & (arr == F.NO_QC)] = F.GOOD
    ds.attrs["qc_config_version"] = cfg["version"]
    ds.attrs["qc_tests_applied"] = ",".join(report.tests_run)
    if reference_time:
        ds.attrs["qc_reference_time"] = reference_time
    return report
