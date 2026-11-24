#!/bin/sh
# Creates the three storage-zone buckets in MinIO. Safe to run repeatedly.
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

for bucket in raw standardised derived; do
  mc mb --ignore-existing "local/$bucket"
  echo "bucket ready: $bucket"
done

# Raw zone is the untouched original: keep every version of every file.
mc version enable local/raw

echo "all zone buckets ready"
