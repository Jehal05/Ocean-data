#!/usr/bin/env python3
"""Copy files into the RAW zone bucket without altering them.

* Object key = path relative to --src (so data/raw/sst_grid/x.nc -> sst_grid/x.nc).
* SHA-256 of every file is stored as object metadata and in MANIFEST.sha256.
* Never overwrites: if a key already exists with a different hash the script
  stops with an error (raw files are the untouched original).
* Re-running with identical files is a no-op.

Usage:
  python scripts/upload_raw.py                       # data/raw  -> raw bucket
  python scripts/upload_raw.py --src data/test_errors --prefix test_errors/
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default="data/raw", type=Path)
    ap.add_argument("--bucket", default="raw")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--manifest-only", action="store_true",
                    help="only (re)write MANIFEST.sha256 from the local files; no network")
    ap.add_argument("--endpoint", default=os.getenv("S3_ENDPOINT", "http://localhost:8333"))
    ap.add_argument("--access-key", default=os.getenv("S3_ACCESS_KEY", "scatsdev"))
    ap.add_argument("--secret-key", default=os.getenv("S3_SECRET_KEY", "scatsdev_secret_pw"))
    a = ap.parse_args()

    files = sorted(p for p in a.src.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    if not files:
        print(f"no files under {a.src}", file=sys.stderr)
        return 1

    if a.manifest_only:
        lines = [f"{sha256(p)}  {a.prefix + p.relative_to(a.src).as_posix()}" for p in files]
        (a.src / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
        print(f"wrote {a.src / 'MANIFEST.sha256'} ({len(lines)} files)")
        return 0

    s3 = boto3.client("s3", endpoint_url=a.endpoint, aws_access_key_id=a.access_key,
                      aws_secret_access_key=a.secret_key, region_name="us-east-1",
                      config=Config(signature_version="s3v4"))
    manifest, uploaded, skipped = [], 0, 0
    for p in files:
        key = a.prefix + p.relative_to(a.src).as_posix()
        digest = sha256(p)
        manifest.append(f"{digest}  {key}")
        try:
            head = s3.head_object(Bucket=a.bucket, Key=key)
            if head.get("Metadata", {}).get("sha256") == digest:
                skipped += 1
                print(f"= unchanged  {key}")
                continue
            print(f"ERROR: {key} already exists in '{a.bucket}' with different content. "
                  "Raw files are never overwritten.", file=sys.stderr)
            return 2
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                raise
        s3.upload_file(str(p), a.bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})
        uploaded += 1
        print(f"+ uploaded   {key}  ({p.stat().st_size / 1024:.0f} KiB)")

    (a.src / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n")
    print(f"\n{uploaded} uploaded, {skipped} unchanged -> s3://{a.bucket}/{a.prefix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
