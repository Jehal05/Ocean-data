#!/usr/bin/env python3
"""Phase 2 acceptance check. Prints PASS / FAIL / SKIP with evidence. SKIP means "could not be verified here".

Exit code 1 if anything FAILs, 2 if nothing failed but something was skipped, 0 if all passed.
Includes the review-round checks (see docs/phase2_design.md section 8). Database-backed checks use the local stand-in PostgreSQL (no PostGIS) unless Docker/Postgres isn't available.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs", "components/02_parser_api", "components/03_qc_framework", "tests"):
    sys.path.insert(0, str(ROOT / p))
warnings.filterwarnings("ignore")
results: list[tuple[str, str, str]] = []


def record(name, status, evidence):
    results.append((name, status, evidence))


def pytest_run(*args) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", *args], capture_output=True, text=True, cwd=ROOT)
    last = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:] or [r.stderr[-200:]]
    return r.returncode == 0, last[0]


def check_parse_raw():
    from scats_common.validate import validate_dataset
    from scats_parsers import parse_file
    name = "all 5 raw files parse with 0 validate_dataset() violations"
    files = sorted(p for p in (ROOT / "data/raw").rglob("*") if p.is_file() and p.suffix in (".nc", ".csv"))
    out = {f.name: len(validate_dataset(parse_file(f))) for f in files}
    record(name, "PASS" if len(out) == 5 and not any(out.values()) else "FAIL", f"violations per file: {out}")


def check_named_tests():
    base = "tests/unit/test_common.py::"
    for name, ids in [
        ("unit conversions match hand-computed values",
         [base + "test_unit_conversions_hand_computed", base + "test_elevation_to_depth_flips_sign",
          base + "test_argo_juld", "tests/unit/test_parsers.py::test_sst_kelvin_to_celsius"]),
        ("fill values become NaN with flag 9; Argo provider flags mapped",
         ["tests/unit/test_parsers.py::test_fill_values_become_nan_flag_9",
          "tests/unit/test_parsers.py::test_argo_provider_flags_mapped",
          "tests/integration/test_ingest_db.py::test_provider_flags_get_their_own_test_id"]),
    ]:
        needs_db = any("integration" in i for i in ids)
        import db_stub
        if needs_db and not db_stub.available():
            record(name, "SKIP", "needs the stand-in PostgreSQL, not available")
            continue
        ok, last = pytest_run(*ids)
        record(name, "PASS" if ok else "FAIL", last)


def check_ten_tests():
    """Each of the ten tests must have a passing AND a failing case, found by node name and actually run."""
    from scats_qc import ALL_TESTS
    name = "all 10 QC tests exist, each with a passing and a failing unit case (run by node name)"
    ids = [t.test_id for t in ALL_TESTS]
    problems = []
    for tid in ids:
        for kind in ("pass", "fail"):
            ok, last = pytest_run("tests/unit/test_qc_each.py", "-k", f"{tid}_{kind}")
            if not ok or " 1 passed" not in last and "1 passed" not in last:
                problems.append(f"{tid}_{kind}: {last}")
    ok, last = pytest_run("tests/unit/test_qc.py")
    record(name, "PASS" if len(ids) == 10 and not problems and ok else "FAIL",
           f"{len(ids)} tests; 20 named cases run one by one, problems: {problems or 'none'}; test_qc.py: {last}")


def check_review_fixes():
    ok, last = pytest_run("tests/unit/test_review_fixes.py")
    record("review fixes (time jumps, determinism, explicit synthetic status, declared units)", "PASS" if ok else "FAIL", last)


def check_lint():
    name = "lint: ruff check (rules F, E9) is clean"
    if not shutil.which("ruff"):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", "ruff"], capture_output=True, text=True)
        if r.returncode:
            return record(name, "SKIP", "ruff not installable here")
    r = subprocess.run(["ruff", "check", "--select", "F,E9", "--output-format", "concise", "components", "libs", "tests", "scripts"],
                       capture_output=True, text=True, cwd=ROOT)
    n = len([l for l in r.stdout.splitlines() if re.match(r".+:\d+:\d+:", l)])
    record(name, "PASS" if r.returncode == 0 else "FAIL", f"{n} findings")


def check_limits_documented():
    name = "documented limits are present where users read them"
    must = {
        "components/03_qc_framework/README.md": ["cannot see the first and last record", "ambiguous", "collateral", "units_assumed_from_mapping", "engineering judgement"],
        "docs/phase2_design.md": ["cannot see the first and last record", "ambiguous", "collateral", "units_assumed_from_mapping", "engineering judgement"],
        "components/02_parser_api/scats_ingest/limits.py": ["cannot see the first and last record", "ambiguous", "neighbours", "units_assumed_from_mapping", "engineering judgement"],
    }
    missing = {f: [k for k in keys if k not in (ROOT / f).read_text()] for f, keys in must.items()}
    missing = {f: m for f, m in missing.items() if m}
    record(name, "PASS" if not missing else "FAIL", f"missing phrases: {missing or 'none'}")


def check_mutation():
    name = "mutation check: removing each exercised QC test makes the score worse"
    r = subprocess.run([sys.executable, str(ROOT / "tests/mutation_check.py")], capture_output=True, text=True, cwd=ROOT)
    if r.returncode == 2:
        return record(name, "SKIP", r.stdout.strip()[:150])
    rep = json.loads((ROOT / "reports/phase2_mutation_check.json").read_text())
    stuck = [t["test_id"] for t in rep["tests"] if t["exercised_by_answer_key"] and not t["primary_fired_dropped"] and not t["recall_dropped"]]
    unexercised = [t["test_id"] for t in rep["tests"] if not t["exercised_by_answer_key"]]
    record(name, "PASS" if r.returncode == 0 and not stuck else "FAIL",
           f"removal changes nothing for exercised tests: {stuck or 'none'}; recall unchanged on removal: {rep['recall_unchanged_when_removed']}; "
           f"not exercised by the answer key (unit tests only): {unexercised}")


def check_blind():
    name = "blind error set (made after the config was frozen) was scored once and reported"
    f = ROOT / "reports/phase2_blind_score.json"
    frozen = ROOT / "reports/qc_config_frozen.sha256"
    if not f.exists() or not frozen.exists():
        return record(name, "FAIL", "report or frozen-config hash file missing")
    rep = json.loads(f.read_text())
    # One lint-only edit was made after the freeze: the unused line "import xarray as xr" was removed from tests.py
    # (ruff F401). To prove nothing else changed, that line is put back in memory and the frozen hash is compared.
    import hashlib
    unchanged = True
    for line in frozen.read_text().splitlines():
        if not re.match(r"^[0-9a-f]{64}\s", line):
            continue
        want, rel = line.split(None, 1)
        data = (ROOT / rel.strip()).read_text()
        if rel.strip().endswith("scats_qc/tests.py"):
            data = data.replace("import pandas as pd\n", "import pandas as pd\nimport xarray as xr\n", 1)
        unchanged &= hashlib.sha256(data.encode()).hexdigest() == want
    ok = unchanged
    record(name, "PASS" if ok else "FAIL",
           f"recall {rep['recall']}; negative controls flagged: {len(rep['negative_control_false_alarms'])}; false positives "
           f"{rep['false_positives_in_error_files']} ({rep['false_positives_by_test']}); config, engine and test code unchanged since the freeze apart from one removed unused import: {unchanged}. "
           f"Rerun is NOT done by this script (the blind set is meant to be scored once); same author, synthetic data.")


def check_score():
    name = "recall on the answer key is 24/24; zero 3/4 flags on the clean raw files"
    r = subprocess.run([sys.executable, str(ROOT / "tests/score_qc.py")], capture_output=True, text=True, cwd=ROOT)
    if r.returncode == 2:
        return record(name, "SKIP", r.stdout.strip()[:200])
    rep = json.loads((ROOT / "reports/phase2_qc_score.json").read_text())
    ok = rep["recall"] == "24/24" and rep["clean_total"] == 0
    record(name, "PASS" if ok else "FAIL",
           f"recall {rep['recall']}; clean-file flags 3/4: {rep['clean_total']}; false positives in error files: "
           f"{rep['false_positives_in_error_files']} (by test {rep['false_positives_by_test']}); collateral density flags "
           f"{rep['collateral_density_flags']} around {rep['culprit_cells_flagged']} culprit cells; expected primary test fired on the cell "
           f"{rep['expected_primary_test_fired_on_the_cell']}; see reports/phase2_qc_score.md. Database: {rep['database']}")


def check_db_behaviour():
    import db_stub
    if not db_stub.available():
        for n in ("re-ingest creates no duplicates", "flag-consistency check passes",
                  "services use a non-superuser role"):
            record(n, "SKIP", "no PostgreSQL available")
        return
    okf, lastf = pytest_run("tests/integration/test_review_fixes_db.py")
    record("review fixes in the database layer (staging after commit, grants, access rule)", "PASS" if okf else "FAIL", lastf)
    ok, last = pytest_run("tests/integration/test_ingest_db.py")
    record("re-ingest creates no duplicates", "PASS" if ok else "FAIL",
           "test_reingest_creates_no_duplicates (+ the rest of test_ingest_db.py): " + last)
    ok2, last2 = pytest_run("tests/integration/test_ingest_db.py::test_flag_consistency_passes_then_detects_tampering")
    record("flag-consistency check passes (and fails when the database is tampered with)", "PASS" if ok2 else "FAIL", last2)
    compose = (ROOT / "docker-compose.yml").read_text()
    uses_ingest = "scats_ingest:" in compose and re.search(r"parser-api:.*?DATABASE_URL: postgresql://scats_ingest", compose, re.S)
    uses_super = re.search(r"DATABASE_URL: postgresql://\$\{POSTGRES_USER", compose)
    ok3, last3 = pytest_run("tests/integration/test_ingest_db.py::test_ingest_role_is_not_a_superuser_and_cannot_alter_history",
                            "tests/integration/test_parser_api.py::test_health_reports_limited_role")
    record("services use a non-superuser role", "PASS" if (uses_ingest and not uses_super and ok3) else "FAIL",
           f"compose parser-api uses scats_ingest: {bool(uses_ingest)}; no service uses the superuser: {not uses_super}; "
           f"role tests: {last3}")


def check_api():
    import db_stub
    name = "parser API works under TestClient"
    if not db_stub.available():
        return record(name, "SKIP", "needs the stand-in PostgreSQL")
    ok, last = pytest_run("tests/integration/test_parser_api.py")
    record(name, "PASS" if ok else "FAIL", last)


def check_compose():
    name = "compose file validates"
    if not shutil.which("docker"):
        return record(name, "SKIP", "docker CLI not installed")
    r = subprocess.run(["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "config", "--services"],
                       capture_output=True, text=True, cwd=ROOT)
    services = set(r.stdout.split())
    ok = r.returncode == 0 and {"db", "storage", "storage-init", "api", "parser-api"} <= services
    record(name, "PASS" if ok else "FAIL",
           f"`docker compose config`: services {sorted(services)} (this validates the file only; images were NOT built "
           f"and no container was started){'' if ok else ' ' + r.stderr[:150]}")
    d = subprocess.run(["docker", "info"], capture_output=True, text=True)
    record("docker compose up builds and runs parser-api", "SKIP",
           "no Docker daemon in this environment" if d.returncode else "not exercised by this script; run `docker compose up --build`")


def check_phase1_regression():
    """Run the Phase 1 acceptance script against stand-ins: stand-in PostgreSQL, moto S3 and the Phase 1 API."""
    import db_stub
    name = "Phase 1 acceptance script still passes (against stand-ins)"
    if not (db_stub.available() and shutil.which("moto_server")):
        return record(name, "SKIP", "needs local PostgreSQL binaries and moto_server")
    db_stub.start(fresh=True)
    env = dict(os.environ, S3_ENDPOINT="http://127.0.0.1:18333", S3_ACCESS_KEY="scatsdev", S3_SECRET_KEY="scatsdev_secret_pw",
               ZONE_BUCKETS="raw,standardised,derived", DATABASE_URL=db_stub.APP_URL, API_URL="http://127.0.0.1:18000",
               AWS_ACCESS_KEY_ID="scatsdev", AWS_SECRET_ACCESS_KEY="scatsdev_secret_pw")
    procs = []
    try:
        procs.append(subprocess.Popen(["moto_server", "-p", "18333"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(3)
        api_dir = ROOT / "components/07_data_access_api"
        subprocess.run([sys.executable, "-m", "app.init_storage"], cwd=api_dir, env=env, check=True, capture_output=True, timeout=60)
        subprocess.run([sys.executable, "scripts/upload_raw.py"], cwd=ROOT, env=env, check=True, capture_output=True, timeout=120)
        subprocess.run([sys.executable, "scripts/upload_raw.py", "--src", "data/test_errors", "--prefix", "test_errors/"],
                       cwd=ROOT, env=env, check=True, capture_output=True, timeout=120)
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "18000"], cwd=api_dir, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(4)
        r = subprocess.run([sys.executable, "tests/check_phase1.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        lines = [l for l in r.stdout.splitlines() if re.match(r"\[(PASS|FAIL|SKIP)", l)]
        counts = {s: sum(1 for l in lines if l.startswith(f"[{s}")) for s in ("PASS", "FAIL", "SKIP")}
        skipped = [l[7:90] for l in lines if l.startswith("[SKIP")]
        failed = [l[7:90] for l in lines if l.startswith("[FAIL")]
        record(name, "PASS" if counts["FAIL"] == 0 and counts["PASS"] >= 5 else "FAIL",
               f"{counts}; failed: {failed or 'none'}; skipped: {skipped or 'none'}. Docker-specific Phase 1 checks stay unverified here.")
    except Exception as exc:  # noqa: BLE001
        record(name, "FAIL", f"could not run: {type(exc).__name__}: {str(exc)[:150]}")
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        db_stub.stop()


def check_nothing_pushed():
    name = "nothing pushed"
    r = subprocess.run(["git", "status", "-sb"], capture_output=True, text=True, cwd=ROOT)
    head = r.stdout.splitlines()[0] if r.stdout else ""
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    remote = subprocess.run(["git", "branch", "-r", "--list", f"origin/{branch}"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    record(name, "PASS" if not remote else "FAIL", f"branch {branch}; remote-tracking branch for it: {remote or 'none'}; {head}")


def check_unit_suite():
    ok, last = pytest_run("tests/unit")
    record("unit test suite", "PASS" if ok else "FAIL", last)


def main() -> int:
    for fn in (check_parse_raw, check_unit_suite, check_named_tests, check_ten_tests, check_review_fixes, check_score,
               check_mutation, check_blind, check_lint, check_limits_documented, check_db_behaviour, check_api, check_compose,
               check_phase1_regression, check_nothing_pushed):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record(fn.__name__, "FAIL", f"check crashed: {type(exc).__name__}: {exc}")
    width = max(len(n) for n, *_ in results)
    for n, s, e in results:
        print(f"{s:5s} {n:<{width}}  {e}")
    fails = sum(1 for _n, s, _e in results if s == "FAIL")
    skips = sum(1 for _n, s, _e in results if s == "SKIP")
    print(f"\n{len(results) - fails - skips} PASS, {fails} FAIL, {skips} SKIP")
    return 1 if fails else 2 if skips else 0


if __name__ == "__main__":
    sys.exit(main())
