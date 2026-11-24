"""One-shot job: wait for the S3 API, create the zone buckets, then exit.

Run as:  python -m app.init_storage
Exit 0  = all zone buckets exist.
Exit 1  = storage never became reachable, or a bucket could not be created.
Turning on versioning for the raw zone is a nice-to-have: if the store does not
support it, a warning is printed and the job still succeeds.
"""
from __future__ import annotations

import os
import sys
import time

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:8333")
ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "scatsdev")
SECRET_KEY = os.getenv("S3_SECRET_KEY", "scatsdev_secret_pw")
BUCKETS = [b for b in os.getenv("ZONE_BUCKETS", "raw,standardised,derived").split(",") if b]
WAIT_SECONDS = int(os.getenv("INIT_WAIT_SECONDS", "90"))


def client():
    return boto3.client(
        "s3", endpoint_url=ENDPOINT, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}),
    )


def wait_for_storage(s3) -> bool:
    deadline = time.time() + WAIT_SECONDS
    last = ""
    while time.time() < deadline:
        try:
            s3.list_buckets()
            return True
        except (BotoCoreError, ClientError) as exc:
            last = str(exc)[:150]
            time.sleep(2)
    print(f"storage not reachable at {ENDPOINT} after {WAIT_SECONDS}s: {last}", file=sys.stderr)
    return False


def main() -> int:
    s3 = client()
    if not wait_for_storage(s3):
        return 1
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    for bucket in BUCKETS:
        if bucket in existing:
            print(f"bucket exists: {bucket}")
            continue
        try:
            s3.create_bucket(Bucket=bucket)
            print(f"bucket created: {bucket}")
        except (BotoCoreError, ClientError) as exc:
            print(f"cannot create bucket {bucket}: {exc}", file=sys.stderr)
            return 1
    if "raw" in BUCKETS:
        try:
            s3.put_bucket_versioning(Bucket="raw", VersioningConfiguration={"Status": "Enabled"})
            print("versioning enabled on raw")
        except (BotoCoreError, ClientError) as exc:
            print(f"WARNING: could not enable versioning on raw ({str(exc)[:120]}). Continuing; "
                  "scripts/upload_raw.py still refuses to overwrite changed files.")
    print("all zone buckets ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
