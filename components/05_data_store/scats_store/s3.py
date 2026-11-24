"""Thin S3 wrapper (boto3). Everything in the project talks to storage only through the S3 API."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import boto3
from botocore.client import Config


class S3Store:
    def __init__(self, endpoint: str | None, access_key: str, secret_key: str, region: str = "us-east-1"):
        self.client = boto3.client(
            "s3", endpoint_url=endpoint or None, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name=region, config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
                                              retries={"max_attempts": 2}))

    @classmethod
    def from_env(cls) -> S3Store:
        return cls(os.getenv("S3_ENDPOINT", "http://localhost:8333"), os.getenv("S3_ACCESS_KEY", "scatsdev"),
                   os.getenv("S3_SECRET_KEY", "scatsdev_secret_pw"))

    def put_file(self, bucket: str, key: str, path: Path) -> None:
        with open(path, "rb") as fh:
            self.client.put_object(Bucket=bucket, Key=key, Body=fh.read())

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=bucket, Key=key, Body=data)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self.client.get_object(Bucket=bucket, Key=key)["Body"].read()

    def sha256(self, bucket: str, key: str) -> tuple[str, int]:
        data = self.get_bytes(bucket, key)
        return hashlib.sha256(data).hexdigest(), len(data)

    def list(self, bucket: str, prefix: str) -> dict[str, int]:
        out: dict[str, int] = {}
        token = None
        while True:
            kw = {"Bucket": bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
            r = self.client.list_objects_v2(**kw)
            for o in r.get("Contents", []):
                out[o["Key"]] = o["Size"]
            if not r.get("IsTruncated"):
                return out
            token = r["NextContinuationToken"]

    def copy(self, bucket: str, src: str, dst: str) -> None:
        self.client.copy_object(Bucket=bucket, Key=dst, CopySource={"Bucket": bucket, "Key": src})

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        keys = list(self.list(bucket, prefix))
        for i in range(0, len(keys), 1000):
            self.client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]]})
        return len(keys)
