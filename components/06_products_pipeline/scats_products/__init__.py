"""Derived products pipeline (Phase 4): approved, published data in, deterministic Zarr products out."""
from .config import ProductsConfig, code_sha256, load_config  # noqa: F401
from .pipeline import BuildResult, ProductError, build_all, build_product, kinds_for  # noqa: F401

CODE_VERSION = "0.1.0"
