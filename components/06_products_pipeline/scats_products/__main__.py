"""python -m scats_products build [--dataset KEY] [--force]   (uses DATABASE_URL, S3_* from the environment)"""
import argparse
import sys

from .pipeline import build_all


def main() -> int:
    ap = argparse.ArgumentParser(prog="scats_products")
    ap.add_argument("command", choices=["build"])
    ap.add_argument("--dataset")
    ap.add_argument("--force", action="store_true", help="build a new version even if nothing changed")
    a = ap.parse_args()
    results = build_all(force=a.force, dataset_key=a.dataset)
    for r in results:
        print(f"{r.status:9s} {r.product_key:60s} {r.detail}")
    return 1 if any(r.status == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
