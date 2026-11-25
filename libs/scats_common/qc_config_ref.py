"""The database records the QC configuration VERSION a dataset was checked with, not its checksum.
This returns the sha256 of the configuration file, but only when that file has the recorded version; otherwise None
(the file that is deployed is then a different one, and naming its checksum would be wrong)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

DEFAULT = Path(__file__).resolve().parents[2] / "components" / "03_qc_framework" / "scats_qc" / "qc_config.yaml"


def qc_config_sha256_for(version: str | None) -> str | None:
    path = Path(os.getenv("QC_CONFIG_PATH", str(DEFAULT)))
    try:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest() if version and str(yaml.safe_load(data).get("version")) == str(version) else None
    except (OSError, ValueError, yaml.YAMLError, AttributeError):
        return None
