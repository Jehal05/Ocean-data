"""Loads and validates qc_config.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("qc_config.yaml")


class ConfigError(ValueError):
    pass


def _need_rationale(section: str, items: dict):
    for name, spec in items.items():
        if not spec.get("rationale") or not spec.get("source"):
            raise ConfigError(f"qc_config: '{section}.{name}' needs a rationale and a source")


def load_config(path: Path | None = None) -> dict:
    cfg = yaml.safe_load(Path(path or DEFAULT_PATH).read_text())
    for key in ("version", "regional_range", "spike", "density_inversion", "timestamp_valid"):
        if key not in cfg:
            raise ConfigError(f"qc_config: missing section '{key}'")
    cfg["version"] = str(cfg["version"])
    _need_rationale("regional_range", cfg["regional_range"])
    _need_rationale("spike.profile.variables", cfg["spike"]["profile"]["variables"])
    _need_rationale("spike.timeseries", cfg["spike"]["timeseries"])
    for name, spec in cfg["regional_range"].items():
        if not spec["min"] < spec["max"]:
            raise ConfigError(f"qc_config: regional_range.{name} has min >= max")
    return cfg
