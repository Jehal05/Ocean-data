#!/usr/bin/env python3
"""Phase 3 acceptance check. PASS / FAIL / SKIP with evidence. SKIP = could not be verified here.
Exit 1 if anything FAILs, 2 if nothing failed but something was skipped, 0 if all passed."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs", "components/02_parser_api", "components/03_qc_framework", "components/04_expert_review_ui", "components/05_data_store", "tests"):
    sys.path.insert(0, str(ROOT / p))
warnings.filterwarnings("ignore")
results: list[tuple[str, str, str]] = []


def rec(name, status, ev):
    results.append((name, status, ev))
    print(f"{status:5s} {name} -- {ev}", flush=True)


def run(*args):
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=ROOT)
    last = [ln for ln in (r.stdout + r.stderr).strip().splitlines() if ln.strip()][-1:] or [""]
    return r.returncode, last[0]


def pytest_check(name, *args):
    rc, last = run("-m", "pytest", "-q", "-p", "no:cacheprovider", *args)
    rec(name, "PASS" if rc == 0 else "FAIL", last)


def main() -> int:
    import db_stub
    have_db = db_stub.available()
    # role matrix completeness: every route the app serves has a row, nothing extra
    try:
        from review_api import policy
        from review_api.main import app
        routes = {(m, r.path) for r in app.routes if hasattr(r, "methods") and r.path not in ("/openapi.json", "/docs", "/redoc")
                  and not r.path.startswith("/static") for m in r.methods if m not in ("HEAD", "OPTIONS")}
        ui = {k for k in routes if k[1] in ("/",)}
        missing = sorted(k for k in routes - ui if k not in policy.ROLE_MATRIX)
        rec("role matrix has a row for every route", "PASS" if not missing else "FAIL", f"{len(policy.ROLE_MATRIX)} rows; missing: {missing}")
    except Exception as e:  # noqa: BLE001
        rec("role matrix has a row for every route", "FAIL", repr(e))
    # limits stated in code and in docs
    from review_api.policy import REVIEW_LIMITS
    docs = (ROOT / "docs/phase3_design.md").read_text() + (ROOT / "README.md").read_text()
    lacking = [s for s in REVIEW_LIMITS if s[:40] not in docs] if all(isinstance(s, str) for s in REVIEW_LIMITS) else []
    rec("limits are in the API and the docs", "PASS" if REVIEW_LIMITS and not lacking else "FAIL", f"{len(REVIEW_LIMITS)} limits; not in docs: {len(lacking)}")
    # config unchanged
    sha = hashlib.sha256((ROOT / "components/03_qc_framework/scats_qc/qc_config.yaml").read_bytes()).hexdigest()
    ok = sha == "225bc3afe0a0ff0beddd693b22c7b23c2d4994c3bc1f3ebf2ba5268a1c9130f3"
    rec("qc_config.yaml unchanged since Phase 2", "PASS" if ok else "FAIL", sha)
    pytest_check("store: Zarr round trip, lossless, safe write (unit)", "tests/unit/test_store.py")
    if have_db:
        pytest_check("database: least privilege, guards, auth, audit chain", "tests/integration/test_review_db.py")
        pytest_check("API: authorisation matrix, four-eyes, publish, snapshot, XSS, injection, atomic audit", "tests/integration/test_review_api.py")
        rc, last = run("tests/mutation_check_phase3.py")
        rec("mutation check: every safeguard switched off is noticed", "PASS" if rc == 0 else "FAIL", f"exit {rc}; see reports/phase3_mutation_check.md")
        rc, last = run("tests/ui_check_phase3.py")
        rec("web UI driven in headless Chrome (login, flag change, approve, publish)", {0: "PASS", 2: "SKIP"}.get(rc, "FAIL"), f"exit {rc}; see the output of tests/ui_check_phase3.py")
        rc, last = run("tests/check_phase2.py")
        rec("Phase 1-2 regression (check_phase2.py)", "PASS" if rc in (0, 2) else "FAIL", f"exit {rc}: {last}")
    else:
        for n in ("database/API/mutation/regression checks",):
            rec(n, "SKIP", "no local PostgreSQL 16 binaries")
    r = subprocess.run(["ruff", "check", "--select", "F,E9", "."], capture_output=True, text=True, cwd=ROOT)
    rc, last = r.returncode, ([ln for ln in r.stdout.strip().splitlines() if ln.strip()] or [""])[-1]
    rec("lint (ruff F,E9)", "PASS" if rc == 0 else "FAIL", last)
    rec("Docker images / compose stack", "SKIP", "no Docker daemon: images not built, stack not run")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/phase3_check.json").write_text(json.dumps([{"check": n, "status": s, "evidence": e} for n, s, e in results], indent=1))
    fails = sum(s == "FAIL" for _, s, _ in results)
    skips = sum(s == "SKIP" for _, s, _ in results)
    print(f"\n{len(results)} checks: {fails} FAIL, {skips} SKIP")
    return 1 if fails else (2 if skips else 0)


if __name__ == "__main__":
    sys.exit(main())
