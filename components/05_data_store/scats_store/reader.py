"""Read a published dataset back from the store, checking every object against its manifest first."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import xarray as xr

from .checksum import manifest_sha256
from .s3 import S3Store
from .writer import MANIFEST_NAME, read_local


class StoreCorrupt(RuntimeError):
    pass


def read_store(s3: S3Store, bucket: str, prefix: str, *, expected_manifest_sha256: str | None = None) -> xr.Dataset:
    """Download the Zarr prefix to a temporary directory (no lazy remote reads yet), verify it, load it."""
    work = Path(tempfile.mkdtemp(prefix="scats_read_"))
    try:
        manifest = json.loads(s3.get_bytes(bucket, f"{prefix}/{MANIFEST_NAME}"))
        objects = {}
        for rel, info in manifest["objects"].items():
            data = s3.get_bytes(bucket, f"{prefix}/{rel}")
            if hashlib.sha256(data).hexdigest() != info["sha256"] or len(data) != info["size"]:
                raise StoreCorrupt(f"object {rel} does not match the manifest")
            dest = work / "store.zarr" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            objects[rel] = (info["sha256"], info["size"])
        digest = manifest_sha256(objects)
        if digest != manifest["manifest_sha256"] or (expected_manifest_sha256 and digest != expected_manifest_sha256):
            raise StoreCorrupt("the manifest checksum does not match the catalogue")
        extra = set(s3.list(bucket, prefix + "/")) - {f"{prefix}/{r}" for r in objects} - {f"{prefix}/{MANIFEST_NAME}"}
        if extra:
            raise StoreCorrupt(f"objects not in the manifest: {sorted(extra)[:3]}")
        return read_local(work / "store.zarr")
    finally:
        shutil.rmtree(work, ignore_errors=True)
