#!/usr/bin/env python3
"""Phase 5 acceptance check. PASS / FAIL / SKIP with evidence. SKIP = could not be verified here.
Exit 1 if anything FAILs, 2 if nothing failed but something was skipped, 0 if all passed."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
QC_SHA = "225bc3afe0a0ff0beddd693b22c7b23c2d4994c3bc1f3ebf2ba5268a1c9130f3"
PHASE4_COMMIT = "4f986ce"
STATIC = ROOT / "components/08_visualization_dashboard/dashboard/static"
results: list[tuple[str, str, str]] = []


def rec(name, status, ev):
    results.append((name, status, ev))
    print(f"{status:5s} {name} -- {ev}", flush=True)


def run(*args, **kw):
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=ROOT, **kw)
    last = [ln for ln in (r.stdout + r.stderr).strip().splitlines() if ln.strip()][-1:] or [""]
    return r.returncode, last[0]


def main() -> int:
    import db_stub
    have_db = db_stub.available()
    sha = hashlib.sha256((ROOT / "components/03_qc_framework/scats_qc/qc_config.yaml").read_bytes()).hexdigest()
    rec("qc_config.yaml unchanged since Phase 2", "PASS" if sha == QC_SHA else "FAIL", sha)
    r = subprocess.run(["git", "diff", "--name-only", PHASE4_COMMIT, "--", "db/init"], capture_output=True, text=True, cwd=ROOT)
    st = subprocess.run(["git", "status", "--porcelain", "db/init"], capture_output=True, text=True, cwd=ROOT).stdout
    rec("migrations 001-007 unchanged", "PASS" if r.returncode == 0 and not r.stdout.strip() and not st.strip() else "FAIL", f"changed: {r.stdout.split()}; status: {st.strip()}")
    served = sorted(p.name for p in STATIC.iterdir())
    bad = [p for p in served if p.endswith((".js", ".html", ".css")) and re.search(r"(?:https?:)?//(?!www\.w3\.org)[A-Za-z0-9.-]+\.[A-Za-z]{2,}", (STATIC / p).read_text())]
    vendored = [p for p in served if p not in ("index.html", "app.js", "api.js", "logic.js", "svg.js", "views.js", "style.css", "package.json")]
    rec("no vendored library, no CDN or other host in any served file", "PASS" if not bad and not vendored else "FAIL", f"files {served}; hosts in {bad}; unexpected {vendored}")
    rc, last = run("node", "--test", "tests/js/logic.test.mjs")
    rec("dashboard logic (Node, hand-computed values)", "PASS" if rc == 0 else "FAIL", last)
    rc, last = run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/unit/test_dashboard_static.py")
    rec("static rules: no innerHTML/eval/inline script or style/CDN", "PASS" if rc == 0 else "FAIL", last)
    if have_db:
        rc, last = run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/integration/test_dashboard_browser.py")
        rec("browser tests: login, views, notices, hostile text, CSP, CSRF, a11y (headless Chrome, real stack)", "PASS" if rc == 0 else "FAIL", last)
        rc, last = run(sys.executable, "tests/mutation_check_phase5.py")
        rec("mutation check: every dashboard safeguard switched off is noticed", "PASS" if rc == 0 else "FAIL", f"exit {rc}; see reports/phase5_mutation_check.md")
        rc, last = run(sys.executable, "tests/check_phase4.py")
        rec("Phase 1-4 regression (check_phase4.py)", "PASS" if rc in (0, 2) else "FAIL", f"exit {rc}: {last}")
    else:
        rec("browser / mutation / regression checks", "SKIP", "no local PostgreSQL 16 binaries")
    r = subprocess.run(["ruff", "check", "--select", "F,E9", "."], capture_output=True, text=True, cwd=ROOT)
    rec("lint (ruff F,E9)", "PASS" if r.returncode == 0 else "FAIL", ([ln for ln in r.stdout.strip().splitlines() if ln.strip()] or [""])[-1])
    has_docker = subprocess.run(["which", "docker"], capture_output=True).returncode == 0
    r = subprocess.run(["docker", "compose", "config", "-q"], capture_output=True, text=True, cwd=ROOT) if has_docker else None
    rec("docker compose file is valid", "SKIP" if r is None else ("PASS" if r.returncode == 0 else "FAIL"), "docker CLI not installed" if r is None else (r.stderr.strip()[-120:] or "ok"))
    rec("Docker images / compose stack run", "SKIP", "no Docker daemon: images not built, healthcheck not run")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/phase5_check.json").write_text(json.dumps([{"check": n, "status": s, "evidence": e} for n, s, e in results], indent=1))
    fails = sum(s == "FAIL" for _, s, _ in results)
    skips = sum(s == "SKIP" for _, s, _ in results)
    print(f"\n{len(results)} checks: {fails} FAIL, {skips} SKIP")
    return 1 if fails else (2 if skips else 0)


if __name__ == "__main__":
    sys.exit(main())
