from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "products_config.yaml"


@dataclass(frozen=True)
class ProductsConfig:
    raw: dict
    sha256: str

    @property
    def good_flags(self) -> tuple[int, ...]:
        return tuple(int(f) for f in self.raw["good_flags"])


def load_config(path: Path | None = None) -> ProductsConfig:
    data = (path or CONFIG_PATH).read_bytes()
    raw = yaml.safe_load(data)
    edges = raw["binned_profile"]["depth_bin_edges_m"]
    if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError("depth_bin_edges_m must be strictly increasing with at least two values")
    if not set(raw["good_flags"]) <= {1, 2}:
        raise ValueError("good_flags may only contain 1 and 2")
    return ProductsConfig(raw, hashlib.sha256(data).hexdigest())


def code_sha256() -> str:
    """sha256 over the source files of this package (names and bytes, sorted), so a code change gives a new product."""
    h = hashlib.sha256()
    for p in sorted(HERE.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()
