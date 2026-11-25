"""Write a reviewed dataset to Zarr in the standardised zone, safely (docs/phase3_design.md section 6).

Order: write locally -> verify the local copy against the source -> upload to a temporary prefix -> download and
verify every object -> copy to the final prefix -> write the manifest last -> delete the temporary prefix.
A failure before the copy leaves the final prefix untouched. A failure during the copy removes what was copied.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import numcodecs
import xarray as xr

from .checksum import content_sha256, datasets_equal, manifest_sha256
from .layout import COMPRESSION, ZARR_FORMAT, chunks_for, store_prefix
from .s3 import S3Store

MANIFEST_NAME = "scats_manifest.json"


class PublishError(RuntimeError):
    """The publish failed. `state` says what was left in the store (normally nothing)."""

    def __init__(self, stage: str, detail: str, state: str = "final location untouched; nothing published"):
        super().__init__(stage, detail, state)
        self.stage, self.detail, self.state = stage, detail, state

    def __str__(self) -> str:
        return f"{self.stage}: {self.detail} ({self.state})"


@dataclass
class PublishResult:
    bucket: str
    prefix: str
    uri: str
    zarr_format: int
    content_sha256: str
    manifest_sha256: str
    n_objects: int
    total_bytes: int


def default_chunker(ds: xr.Dataset):
    kind = ds.attrs["dataset_type"]
    return lambda name, dims, shape, itemsize: chunks_for(kind, dims, shape, itemsize)


def _encoding(ds: xr.Dataset, chunker=None) -> dict:
    chunker = chunker or default_chunker(ds)
    comp = numcodecs.Blosc(cname=COMPRESSION["cname"], clevel=COMPRESSION["clevel"], shuffle=numcodecs.Blosc.SHUFFLE)
    enc: dict = {}
    for n, v in ds.variables.items():
        e = {"chunks": chunker(n, v.dims, v.shape, v.dtype.itemsize), "compressors": (comp,)}
        if v.dtype.kind == "M":
            e.update(dtype="int64", units="nanoseconds since 1970-01-01", _FillValue=-9223372036854775808)
        enc[n] = e
    return enc


def write_local(ds: xr.Dataset, root: Path, chunker=None) -> Path:
    path = root / "store.zarr"
    ds.to_zarr(path, mode="w", zarr_format=ZARR_FORMAT, consolidated=True, encoding=_encoding(ds, chunker))
    return path


def read_local(path: Path) -> xr.Dataset:
    out = xr.open_zarr(path, consolidated=True, chunks=None).load()
    out.close()
    return out


def _files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*")) if p.is_file()}


def _sha(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def publish_zarr(ds: xr.Dataset, *, s3: S3Store, bucket: str, platform_type: str, dataset_key: str) -> PublishResult:
    """ds is the final dataset (see build.for_zarr). Raises PublishError; never leaves a half-written final prefix."""
    return write_zarr_safe(ds, s3=s3, bucket=bucket, final=store_prefix(platform_type, dataset_key))


def write_zarr_safe(ds: xr.Dataset, *, s3: S3Store, bucket: str, final: str, chunker=None) -> PublishResult:
    """The safe write used for standardised datasets (Phase 3) and derived products (Phase 4).

    `final` is the prefix inside `bucket`; `chunker(name, dims, shape, itemsize)` gives the chunk shape of one array."""
    tmp_prefix = f".tmp/{uuid.uuid4().hex}/{final}"
    work = Path(tempfile.mkdtemp(prefix="scats_zarr_"))
    committed: list[str] = []
    stage = "build"
    try:
        local = write_local(ds, work, chunker)
        stage = "verify_local"
        back = read_local(local)
        diff = datasets_equal(ds, back)
        want = content_sha256(ds)
        if diff or content_sha256(back) != want:
            raise PublishError(stage, f"the Zarr copy does not equal the source: {diff[:3]}")
        files = _files(local)
        objects = {rel: _sha(p) for rel, p in files.items()}

        stage = "upload"
        for rel, p in files.items():
            s3.put_file(bucket, f"{tmp_prefix}/{rel}", p)
        stage = "verify_upload"
        for rel, (sha, size) in objects.items():
            got = s3.sha256(bucket, f"{tmp_prefix}/{rel}")
            if got != (sha, size):
                raise PublishError(stage, f"object {rel} differs after upload")

        stage = "commit"
        s3.delete_prefix(bucket, final + "/")                  # objects of an earlier failed attempt (no catalogue row)
        for rel in objects:
            s3.copy(bucket, f"{tmp_prefix}/{rel}", f"{final}/{rel}")
            committed.append(f"{final}/{rel}")
        mdigest = manifest_sha256(objects)
        manifest = {"zarr_format": ZARR_FORMAT, "content_sha256": want, "manifest_sha256": mdigest,
                    "objects": {k: {"sha256": v[0], "size": v[1]} for k, v in sorted(objects.items())}}
        s3.put_bytes(bucket, f"{final}/{MANIFEST_NAME}", json.dumps(manifest, indent=1).encode())
        committed.append(f"{final}/{MANIFEST_NAME}")
        listed = s3.list(bucket, final + "/")
        expected = {f"{final}/{rel}": size for rel, (_, size) in objects.items()}
        if {k: v for k, v in listed.items() if not k.endswith(MANIFEST_NAME)} != expected:
            raise PublishError(stage, "the final prefix does not list the expected objects")
        stage = "cleanup"
        s3.delete_prefix(bucket, tmp_prefix + "/")
        return PublishResult(bucket, final, f"s3://{bucket}/{final}", ZARR_FORMAT, want, mdigest, len(objects) + 1,
                             sum(v[1] for v in objects.values()))
    except PublishError as exc:
        _undo(s3, bucket, final, tmp_prefix, committed, exc)
        raise
    except Exception as exc:  # noqa: BLE001 - anything: report the stage and what state was left
        err = PublishError(stage, f"{type(exc).__name__}: {exc}")
        _undo(s3, bucket, final, tmp_prefix, committed, err)
        raise err from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _undo(s3: S3Store, bucket: str, final: str, tmp_prefix: str, committed: list[str], err: PublishError) -> None:
    """Best effort: remove what this attempt created. Says in the error text what is left if that also fails."""
    left = []
    if committed:
        try:
            s3.delete_prefix(bucket, final + "/")
        except Exception as exc:  # noqa: BLE001
            left.append(f"final prefix {final}/ may hold {len(committed)} partial objects ({type(exc).__name__})")
    try:
        s3.delete_prefix(bucket, tmp_prefix + "/")
    except Exception as exc:  # noqa: BLE001
        left.append(f"temporary prefix {tmp_prefix}/ not removed ({type(exc).__name__})")
    if left:
        err.state = "; ".join(left)
    else:
        err.state = "partial copy removed; nothing published" if committed else "final location untouched; nothing published"
