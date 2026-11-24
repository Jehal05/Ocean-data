"""QC test interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import xarray as xr

from scats_common import MODEL
from scats_common.flags import BAD, PROBABLY_BAD


@dataclass
class Finding:
    test_id: str
    variable: str                 # common name, or "position" / "time"
    index: tuple[int, ...]        # index into the variable's flag array
    flag: int
    detail: dict = field(default_factory=dict)


@dataclass
class TestResult:
    findings: list[Finding] = field(default_factory=list)
    covered: dict[str, np.ndarray] = field(default_factory=dict)      # variable -> bool mask of cells examined


class QCTest(ABC):
    test_id: str
    category: str
    severity: str                 # "hard" -> flag 4, "soft" -> flag 3
    description: str

    @property
    def fail_flag(self) -> int:
        return BAD if self.severity == "hard" else PROBABLY_BAD

    @abstractmethod
    def run(self, ds: xr.Dataset, cfg: dict) -> TestResult: ...

    # -- helpers
    def finding(self, cfg, variable, index, detail, flag=None) -> Finding:
        detail = {"test": self.test_id, "config_version": cfg["version"], **detail}
        return Finding(self.test_id, variable, tuple(int(i) for i in index), self.fail_flag if flag is None else flag, detail)


def model_variables(ds: xr.Dataset) -> list[str]:
    return [v for v in MODEL.variables if v in ds.data_vars]


def has_levels(ds: xr.Dataset) -> bool:
    return "level" in ds.dims


def py(x):
    """numpy scalar -> JSON-friendly python."""
    x = np.asarray(x)
    return x.item() if x.ndim == 0 else x.tolist()
