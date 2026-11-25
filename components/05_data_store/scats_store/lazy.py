"""Read a published Zarr store lazily from S3 (Phase 4).

Only the objects Zarr asks for are fetched. Each one is checked against the manifest (sha256 and size) when it arrives,
and the manifest itself is checked against the checksum in the catalogue before anything else is trusted.
Keys that are not listed in the manifest are never requested from S3, so a caller cannot make this read another prefix.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Iterable

import xarray as xr
from zarr.abc.buffer import Buffer, BufferPrototype
from zarr.abc.store import ByteRequest, OffsetByteRequest, RangeByteRequest, Store, SuffixByteRequest

from .checksum import manifest_sha256
from .reader import StoreCorrupt
from .s3 import S3Store
from .writer import MANIFEST_NAME


class LazyDeadline(RuntimeError):
    """The request used up its time budget."""


class S3ReadStore(Store):
    supports_writes = False
    supports_deletes = False
    supports_listing = True

    def __init__(self, s3: S3Store, bucket: str, prefix: str, manifest: dict, *, deadline: float | None = None):
        super().__init__(read_only=True)
        self._s3, self._bucket, self._prefix = s3, bucket, prefix.strip("/")
        self._objects: dict[str, dict] = manifest["objects"]
        self._cache: dict[str, bytes] = {}
        self.fetched: list[str] = []                   # objects read from S3, in order (each at most once)
        self.fetched_bytes = 0
        self.deadline = deadline

    def __eq__(self, other: object) -> bool:
        return isinstance(other, S3ReadStore) and (other._bucket, other._prefix) == (self._bucket, self._prefix)

    __hash__ = None  # type: ignore[assignment]

    def with_deadline(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds

    def _fetch(self, key: str) -> bytes | None:
        info = self._objects.get(key)
        if info is None:
            return None
        if key in self._cache:
            return self._cache[key]
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise LazyDeadline("the request ran out of time while reading the store")
        data = self._s3.get_bytes(self._bucket, f"{self._prefix}/{key}")
        if len(data) != info["size"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise StoreCorrupt(f"object {key} does not match the manifest")
        self._cache[key] = data
        self.fetched.append(key)
        self.fetched_bytes += len(data)
        return data

    async def get(self, key: str, prototype: BufferPrototype, byte_range: ByteRequest | None = None) -> Buffer | None:
        data = await asyncio.to_thread(self._fetch, key)
        if data is None:
            return None
        if isinstance(byte_range, RangeByteRequest):
            data = data[byte_range.start:byte_range.end]
        elif isinstance(byte_range, OffsetByteRequest):
            data = data[byte_range.offset:]
        elif isinstance(byte_range, SuffixByteRequest):
            data = data[-byte_range.suffix:]
        return prototype.buffer.from_bytes(data)

    async def get_partial_values(self, prototype: BufferPrototype, key_ranges: Iterable[tuple[str, ByteRequest | None]]) -> list[Buffer | None]:
        return [await self.get(k, prototype, r) for k, r in key_ranges]

    async def exists(self, key: str) -> bool:
        return key in self._objects

    async def set(self, key: str, value: Buffer) -> None:
        raise PermissionError("the store is read-only")

    async def delete(self, key: str) -> None:
        raise PermissionError("the store is read-only")

    async def list(self) -> AsyncIterator[str]:
        for k in sorted(self._objects):
            yield k

    async def list_prefix(self, prefix: str) -> AsyncIterator[str]:
        for k in sorted(self._objects):
            if k.startswith(prefix):
                yield k

    async def list_dir(self, prefix: str) -> AsyncIterator[str]:
        seen: set[str] = set()
        p = prefix.rstrip("/") + "/" if prefix else ""
        for k in sorted(self._objects):
            if k.startswith(p):
                head = k[len(p):].split("/", 1)[0]
                if head not in seen:
                    seen.add(head)
                    yield head


def open_lazy(s3: S3Store, bucket: str, prefix: str, *, expected_manifest_sha256: str, deadline_s: float | None = None
              ) -> tuple[xr.Dataset, S3ReadStore, dict]:
    """(dataset, store, manifest). The dataset reads from S3 only when values are needed (`.load()` / `.values`)."""
    prefix = prefix.strip("/")
    raw = s3.get_bytes(bucket, f"{prefix}/{MANIFEST_NAME}")
    manifest = json.loads(raw)
    objects = {rel: (i["sha256"], i["size"]) for rel, i in manifest["objects"].items()}
    digest = manifest_sha256(objects)
    if digest != manifest["manifest_sha256"] or digest != expected_manifest_sha256:
        raise StoreCorrupt("the manifest checksum does not match the catalogue")
    store = S3ReadStore(s3, bucket, prefix, manifest)
    if deadline_s is not None:
        store.with_deadline(deadline_s)
    ds = xr.open_zarr(store, zarr_format=manifest.get("zarr_format", 2), consolidated=True, chunks=None)
    return ds, store, manifest
