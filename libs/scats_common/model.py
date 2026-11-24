"""Loads data_model.yaml into simple objects."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

YAML_PATH = Path(__file__).with_name("data_model.yaml")


@dataclass(frozen=True)
class Variable:
    name: str
    standard_name: str
    units: str
    valid_min: float
    valid_max: float
    long_name: str


class DataModel:
    def __init__(self, path: Path = YAML_PATH):
        raw = yaml.safe_load(Path(path).read_text())
        self.version: str = str(raw["data_model_version"])
        self.conventions: str = raw["conventions"]
        self.variables: dict[str, Variable] = {
            v["name"]: Variable(v["name"], v["standard_name"], str(v["units"]), float(v["valid_min"]),
                                float(v["valid_max"]), v["long_name"])
            for v in raw["variables"]}
        self.coordinates: dict = raw["coordinates"]
        self.flag_scheme: dict[int, str] = {int(k): v for k, v in raw["flag_scheme"].items()}
        self.vocabularies: dict[str, list[str]] = raw["vocabularies"]
        self.required_dataset_attrs: list[str] = raw["required_dataset_attrs"]
        self.required_variable_attrs: list[str] = raw["required_variable_attrs"]

    def variable(self, name: str) -> Variable:
        try:
            return self.variables[name]
        except KeyError:
            raise KeyError(f"'{name}' is not a standard variable of data model v{self.version}") from None


MODEL = DataModel()
