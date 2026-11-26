#!/usr/bin/env python3
"""Phase 4 acceptance check. PASS / FAIL / SKIP with evidence. SKIP = could not be verified here.
Exit 1 if anything FAILs, 2 if nothing failed but something was skipped, 0 if all passed."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs", "components/02_parser_api", "components/03_qc_framework", "components/04_expert_review_ui", "components/05_data_store",
          "components/06_products_pipeline", "components/07_data_access_api", "tests"):
    sys.path.insert(0, str(ROOT / p))
warnings.filterwarnings("ignore")
results: list[tuple[str, str, str]] = []
PHASE3_COMMIT = "1dc9a84"
QC_SHA = "225bc3afe0a0ff0beddd693b22c7b23c2d4994c3bc1f3ebf2ba5268a1c9130f3"
U = "tests/unit/"
I = "tests/integration/"


def rec(name, status, ev):
    results.append((name, status, ev))
    print(f"{status:5s} {name} -- {ev}", flush=True)


def run(*args):
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=ROOT)
    last = [ln for ln in (r.stdout + r.stderr).strip().splitlines() if ln.strip()][-1:] or [""]
    return r.returncode, last[0]


def pytest_check(name, files, k=None):
    args = ["-m", "pytest", "-q", "-p", "no:cacheprovider", *files] + (["-k", k] if k else [])
    rc, last = run(*args)
    if rc == 5:                                      # nothing was selected: a check that runs no test proves nothing
        rec(name, "FAIL", f"no test matched -k {k!r}")
    else:
        rec(name, "PASS" if rc == 0 else "FAIL", last)


def main() -> int:
    import db_stub
    have_db = db_stub.available()
    # ---- static checks -------------------------------------------------------------------------------------------
    try:
        from access_api import policy
        from access_api.main import app
        routes = {(m, r.path) for r in app.routes if hasattr(r, "methods") for m in r.methods if m not in ("HEAD", "OPTIONS")}
        missing = sorted(k for k in routes if k not in policy.ROLE_MATRIX)
        extra = sorted(k for k in policy.ROLE_MATRIX if k not in routes)
        rec("role matrix has a row for every route and no row without a route", "PASS" if not missing and not extra and routes else "FAIL",
            f"{len(routes)} routes, {len(policy.ROLE_MATRIX)} rows; missing {missing}; extra {extra}")
        rec("docs and openapi are off", "PASS" if app.docs_url is None and app.openapi_url is None and app.redoc_url is None else "FAIL",
            f"docs_url={app.docs_url} openapi_url={app.openapi_url}")
        docs = "".join((ROOT / f).read_text() for f in ("README.md", "docs/phase4_design.md", "components/07_data_access_api/README.md"))
        lacking = [s for s in policy.LIMITS_STATEMENTS if s not in docs]
        defaults_ok = all(f"{name}" in docs for name in policy.DEFAULT_LIMITS)
        rec("limits are stated in code and in docs", "PASS" if policy.LIMITS_STATEMENTS and not lacking and defaults_ok else "FAIL",
            f"{len(policy.LIMITS_STATEMENTS)} statements, not in docs: {len(lacking)}; every limit name in docs: {defaults_ok}")
    except Exception as e:  # noqa: BLE001
        rec("role matrix has a row for every route and no row without a route", "FAIL", repr(e))
    sha = hashlib.sha256((ROOT / "components/03_qc_framework/scats_qc/qc_config.yaml").read_bytes()).hexdigest()
    rec("qc_config.yaml unchanged since Phase 2", "PASS" if sha == QC_SHA else "FAIL", sha)
    r = subprocess.run(["git", "diff", "--name-only", PHASE3_COMMIT, "--", "db/init", "components/04_expert_review_ui", "components/03_qc_framework"],
                       capture_output=True, text=True, cwd=ROOT)
    st = subprocess.run(["git", "status", "--porcelain", "db/init"], capture_output=True, text=True, cwd=ROOT).stdout
    changed = [ln for ln in r.stdout.split() if ln]
    added = [ln[3:] for ln in st.splitlines()]
    ok = r.returncode == 0 and all(c.endswith("007_products_access.sql") for c in changed) and all(a.endswith("007_products_access.sql") for a in added)   # 007 is the only addition; once committed it shows as "changed" since Phase 3
    rec("migrations 001-006, the review API and QC are unchanged since Phase 3 (007 is additive)", "PASS" if ok else "FAIL", f"changed: {changed}; db/init status: {added}")
    # ---- unit tests (no database) --------------------------------------------------------------------------------
    pytest_check("products: hand-computed values, good-flag filter (unit)", [U + "test_products.py"])
    pytest_check("access: filters, hostile values, engine, outputs, lazy store (unit)", [U + "test_access.py"])
    if have_db:
        pytest_check("least-privilege roles scats_products / scats_access (parametrised denials)", [I + "test_products_db.py"], "products_role_cannot or access_role_cannot or what_the_two_roles or append_only or limit")
        pytest_check("database: stale triggers, views, versions", [I + "test_products_db.py"])
        pytest_check("published-only input", [I + "test_products_pipeline.py"], "only_published or reads_only_the_standardised")
        pytest_check("lineage record", [I + "test_products_pipeline.py"], "lineage or qc_config_checksum")
        pytest_check("determinism of every product kind", [I + "test_products_pipeline.py"], "same_inputs_and_code")
        pytest_check("stale marking and rebuild", [I + "test_products_pipeline.py"], "stale or supersedes or republish")
        pytest_check("safe write of derived products (failure injection)", [I + "test_products_pipeline.py"], "failed_upload or corrupted_upload or catalogue_failure or racing or never_overwritten or during_the_build")
        pytest_check("product pipeline: whole file", [I + "test_products_pipeline.py"])
        for name, k in (("authentication", "login or bad_tokens or disabled_user or review_api or logout"),
                        ("authorisation matrix (every role x every route)", "role_matrix or policy_row or docs_and_openapi"),
                        ("lazy subset read (objects counted)", "small_request or changed_object or does_not_match"),
                        ("limits enforced (413/422/429/504, truncated pages say so)", "limit or time_budget or paging_says or rate_limit"),
                        ("hostile filters never give 500 or run as SQL", "hostile or search_text or control_character or bad_search"),
                        ("CSV formula injection", "formulas or minus"),
                        ("query-log atomicity", "log_row or log_code or answered_request"),
                        ("good-flag filter through the API", "quality_good or hand_checked_profile"),
                        ("stale product flagged and not served", "stale"),
                        ("download round trip (CSV, NetCDF, product, headers, names)", "download or netcdf or server_made"),
                        ("answers carry data_version / product_version / qc sha / flags", "citation or metadata_carries or version_headers")):
            pytest_check(name, [I + "test_access_api.py"], k)
        pytest_check("access API: whole file", [I + "test_access_api.py"])
        rc, last = run("tests/mutation_check_phase4.py")
        rec("mutation check: every safeguard switched off is noticed", "PASS" if rc == 0 else "FAIL", f"exit {rc}; see reports/phase4_mutation_check.md")
        rc, last = run("tests/check_phase3.py")
        rec("Phase 1-3 regression (check_phase3.py)", "PASS" if rc in (0, 2) else "FAIL", f"exit {rc}: {last}")
    else:
        rec("database / API / mutation / regression checks", "SKIP", "no local PostgreSQL 16 binaries")
    r = subprocess.run(["ruff", "check", "--select", "F,E9", "."], capture_output=True, text=True, cwd=ROOT)
    rec("lint (ruff F,E9)", "PASS" if r.returncode == 0 else "FAIL", ([ln for ln in r.stdout.strip().splitlines() if ln.strip()] or [""])[-1])
    r = subprocess.run(["docker", "compose", "config", "-q"], capture_output=True, text=True, cwd=ROOT) if subprocess.run(["which", "docker"], capture_output=True).returncode == 0 else None
    rec("docker compose file is valid", "SKIP" if r is None else ("PASS" if r.returncode == 0 else "FAIL"), "docker CLI not installed" if r is None else (r.stderr.strip()[-120:] or "ok"))
    rec("Docker images / compose stack run", "SKIP", "no Docker daemon: images not built, stack not run; SeaweedFS and PostGIS behaviour not verified (moto and plain PostgreSQL stand in)")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/phase4_check.json").write_text(json.dumps([{"check": n, "status": s, "evidence": e} for n, s, e in results], indent=1))
    fails = sum(s == "FAIL" for _, s, _ in results)
    skips = sum(s == "SKIP" for _, s, _ in results)
    print(f"\n{len(results)} checks: {fails} FAIL, {skips} SKIP")
    return 1 if fails else (2 if skips else 0)


if __name__ == "__main__":
    sys.exit(main())
