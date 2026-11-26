#!/usr/bin/env python3
"""Start a complete stand-in stack for the dashboard: PostgreSQL 16 stand-in (no PostGIS), moto S3, the data published through the
real review flow, products built, and the access API served by uvicorn. One command, used by the demo and by the browser tests.

    python3 tests/dash_stack.py [--port 18080] [--tiny]

--tiny adds the small hand-made datasets (hostile names, a stale product, a wrap-around SST) that the browser tests check by hand.
Without it, only the five synthetic files in data/raw are loaded: that is the demo. Prints READY <url> when it is up; stop with Ctrl-C."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs", "components/02_parser_api", "components/03_qc_framework", "components/04_expert_review_ui", "components/05_data_store",
          "components/06_products_pipeline", "components/07_data_access_api", "tests"):
    sys.path.insert(0, str(ROOT / p))

S3_PORT = 18333


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--tiny", action="store_true")
    args = ap.parse_args()
    import boto3
    from _pytest.monkeypatch import MonkeyPatch

    import access_helpers as A
    import db_stub
    if not (db_stub.available() and shutil.which("moto_server")):
        print("needs the local PostgreSQL 16 binaries and moto_server (stand-ins); Docker is not used here", file=sys.stderr)
        return 3
    procs: list[subprocess.Popen] = []

    def stop(*_):
        for pr in procs:
            pr.terminate()
        db_stub.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    db_stub.start(fresh=True)
    db_stub.reset_data()
    procs.append(subprocess.Popen(["moto_server", "-p", str(S3_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    endpoint = f"http://127.0.0.1:{S3_PORT}"
    for _ in range(60):
        try:
            urllib.request.urlopen(endpoint, timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    for b in ("raw", "standardised", "derived"):
        s3.create_bucket(Bucket=b)
    mp = MonkeyPatch()
    tmp = Path(tempfile.mkdtemp(prefix="scats_dash_"))
    world = SimpleNamespace(staging=tmp / "staging")
    world.review = A.reviewer_client(tmp, mp)
    for k, v in (("S3_ENDPOINT", endpoint), ("S3_ACCESS_KEY", "testing"), ("S3_SECRET_KEY", "testing")):
        os.environ[k] = v
    for f in (A.ARGO, A.CTD, A.BUOY, A.SST, A.BATHY):
        A.publish(world, f)
    if args.tiny:
        A.register(A.tiny_series(), "hostile")
        A.register(A.tiny_profiles(), "stale_target")
        A.register(A.tiny_profiles(), "unpub", status="in_review")
    os.environ["PRODUCTS_DATABASE_URL"] = db_stub.PRODUCTS_URL
    from scats_products import build_all
    bad = [r for r in build_all() if r.status == "failed"]
    if bad:
        print("product build failed:", [r.detail for r in bad], file=sys.stderr)
        stop()
    env = dict(os.environ, DATABASE_URL=db_stub.ACCESS_URL, AUTH_COOKIE_SECURE="false",
               PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ("libs", "components/02_parser_api", "components/03_qc_framework",
                                                                  "components/05_data_store", "components/07_data_access_api")))
    procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "access_api.main:app", "--port", str(args.port), "--log-level", "warning"], env=env,
                                  cwd=ROOT / "components/07_data_access_api"))
    url = f"http://127.0.0.1:{args.port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/health/live", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    else:
        print("the API did not start", file=sys.stderr)
        stop()
    print(f"READY {url}", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
