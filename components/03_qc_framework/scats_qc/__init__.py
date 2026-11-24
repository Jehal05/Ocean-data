"""SCATS QC framework: ten automated tests, a versioned config and an engine."""
from .base import Finding, QCTest  # noqa: F401
from .config import load_config  # noqa: F401
from .engine import QCReport, run_qc, flag_name  # noqa: F401
from .tests import ALL_TESTS  # noqa: F401
