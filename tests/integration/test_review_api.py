"""Phase 3 review service end to end: stand-in PostgreSQL 16 (no PostGIS), moto S3, FastAPI TestClient."""
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from moto import mock_aws

import db_stub
from review_helpers import (BUOY, BUOY_KEY, CTD, CTD_KEY, NEW_PASSWORD, add_user, cell_of, flagged, ingest, login, make_bucket,
                            set_env, sql, to_in_review)

pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")


@pytest.fixture(scope="module", autouse=True)
def server():
    db_stub.start(fresh=True)
    yield
    db_stub.stop()


@pytest.fixture(autouse=True)
def clean():
    db_stub.reset_data()


@pytest.fixture
def env(tmp_path, monkeypatch):
    set_env(monkeypatch, tmp_path / "st")
    with mock_aws():
        make_bucket()
        from review_api.main import app
        yield SimpleNamespace(client=TestClient(app, raise_server_exceptions=False), staging=tmp_path / "st", tmp=tmp_path,
                              monkeypatch=monkeypatch, app=app)


@pytest.fixture
def hdr(env):
    return SimpleNamespace(admin=login(env.client, "admin"), reviewer=login(env.client, "reviewer"), viewer=login(env.client, "viewer"))


@pytest.fixture
def buoy(env):
    ingest(BUOY, env.staging)
    return BUOY_KEY


@pytest.fixture
def review(env, hdr, buoy):
    to_in_review(env.client, hdr.reviewer, buoy)
    return buoy


def post(env, h, path, body=None):
    return env.client.post(path, headers=h, json=body)


def human_rows():
    return sql("SELECT count(*) FROM qc_flag WHERE set_by_user_id IS NOT NULL")[0][0]


def status_of(key=BUOY_KEY):
    return sql("SELECT status FROM dataset WHERE dataset_key = %s", (key,))[0][0]


def approve(env, h, key, **kw):
    return post(env, h, f"/datasets/{key}/review", {"decision": "approved", "comment": "ok", "accept_unreviewed_flag4": True, **kw})


# ================================================================ access control ==
def test_every_route_has_a_policy_row_and_every_row_a_route(env):
    from review_api.policy import ROLE_MATRIX
    routes = {(m, r.path) for r in env.app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert routes == set(ROLE_MATRIX), (sorted(routes ^ set(ROLE_MATRIX)))


def test_a_route_without_a_policy_row_is_refused(env, hdr):
    env.app.add_api_route("/zz-new", lambda: {"data": 1}, methods=["GET"])
    try:
        for h in (None, hdr.admin):
            r = env.client.get("/zz-new", headers=h)
            assert r.status_code == 403 and "no access rule" in r.text
    finally:
        env.app.router.routes.pop()


BODY = {
    ("POST", "/datasets/{dataset_key}/flags"): {"cells": [{"observation_id": 1, "variable": "a"}], "flag": 1, "comment": "c"},
    ("POST", "/datasets/{dataset_key}/flags/confirm"): {"cells": [{"observation_id": 1, "variable": "a"}], "comment": "c"},
    ("POST", "/datasets/{dataset_key}/review"): {"decision": "rejected", "comment": "c"},
    ("POST", "/admin/users"): {"username": "zz", "password": "short", "role": "viewer"},
    ("POST", "/admin/users/{username}/reset-password"): {"password": "short"},
    ("POST", "/admin/users/{username}/role"): {"role": "viewer"},
}


def test_role_matrix_every_role_against_every_endpoint(env):
    from review_api.policy import ROLE_MATRIX
    from scats_auth import ROLE_ORDER
    checked = 0
    for (method, path), need in sorted(ROLE_MATRIX.items(), key=str):
        if need is None:
            continue
        url = path.format(dataset_key="nope", observation_id=1, username="nobody")
        for role in (None, "viewer", "reviewer", "admin"):
            h = login(env.client, role) if role else None
            r = env.client.request(method, url, headers=h, json=BODY.get((method, path)))
            allowed = role is not None and ROLE_ORDER[role] >= ROLE_ORDER[need]
            who = f"{role or 'anonymous'} -> {method} {path}"
            if allowed:
                assert r.status_code not in (401, 403), f"{who} should be allowed, got {r.status_code} {r.text[:120]}"
            else:
                assert r.status_code == (401 if role is None else 403), f"{who} should be refused, got {r.status_code}"
            checked += 1
    assert checked == 4 * sum(1 for v in ROLE_MATRIX.values() if v)


def test_public_routes_need_no_token(env):
    for p in ("/health", "/auth/info", "/", "/static/app.js", "/static/style.css"):
        assert env.client.get(p).status_code == 200, p
    assert env.client.get("/static/../../etc/passwd").status_code in (404, 422)
    assert env.client.get("/static/secret.txt").status_code == 404
    assert env.client.get("/docs").status_code == 404 and env.client.get("/openapi.json").status_code == 404


# ===================================================================== login ==
def test_login_returns_a_token_and_a_hardened_cookie(env):
    r = env.client.post("/auth/login", json={"username": "reviewer", "password": "reviewer123"})
    assert r.status_code == 200 and r.json()["user"] == {"username": "reviewer", "role": "reviewer"}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "scats_session=" in cookie
    assert "password" not in r.text.lower() or "reviewer123" not in r.text


def test_wrong_password_and_unknown_user_look_the_same(env):
    a = env.client.post("/auth/login", json={"username": "reviewer", "password": "nope"})
    b = env.client.post("/auth/login", json={"username": "nobody", "password": "nope"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_rate_limit_blocks_even_the_right_password_then_recovers(env):
    for _ in range(5):
        assert env.client.post("/auth/login", json={"username": "viewer", "password": "bad"}).status_code == 401
    assert env.client.post("/auth/login", json={"username": "viewer", "password": "viewer123"}).status_code == 429
    sql("UPDATE login_attempt SET attempted_at = now() - interval '1 hour'", fetch=False)
    assert env.client.post("/auth/login", json={"username": "viewer", "password": "viewer123"}).status_code == 200
    assert sql("SELECT count(*) FROM audit_log WHERE action IN ('auth.login_failed', 'auth.login_blocked')")[0][0] == 6


def test_rate_limit_per_address(env):
    env.monkeypatch.setenv("AUTH_MAX_FAILURES_IP", "3")
    for i in range(3):
        env.client.post("/auth/login", json={"username": f"nobody{i}", "password": "bad"})
    assert env.client.post("/auth/login", json={"username": "viewer", "password": "viewer123"}).status_code == 429


def test_failed_logins_are_audited_without_the_password(env, caplog):
    secret = "Very-Secret-Guess-99"
    env.client.post("/auth/login", json={"username": "viewer", "password": secret})
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'auth.login_failed'") == [(1,)]
    assert sql("SELECT count(*) FROM audit_log WHERE comment LIKE %s OR after_state::text LIKE %s", (f"%{secret}%", f"%{secret}%")) == [(0,)]
    assert secret not in caplog.text


def test_disabled_user_cannot_log_in_and_loses_tokens(env, hdr):
    assert env.client.get("/auth/me", headers=hdr.viewer).status_code == 200
    assert env.client.post("/admin/users/viewer/disable", headers=hdr.admin).status_code == 200
    assert env.client.get("/auth/me", headers=hdr.viewer).status_code == 401
    assert env.client.post("/auth/login", json={"username": "viewer", "password": "viewer123"}).status_code == 401
    assert env.client.post("/admin/users/viewer/enable", headers=hdr.admin).status_code == 200
    assert env.client.post("/auth/login", json={"username": "viewer", "password": "viewer123"}).status_code == 200


def test_expired_and_revoked_tokens_are_refused(env, hdr):
    sql("UPDATE auth_session SET expires_at = now() - interval '1 second' WHERE user_id = 3", fetch=False)
    assert env.client.get("/auth/me", headers=hdr.viewer).status_code == 401
    assert env.client.post("/auth/logout", headers=hdr.reviewer).status_code == 200
    assert env.client.get("/auth/me", headers=hdr.reviewer).status_code == 401
    assert env.client.get("/auth/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_cookie_session_needs_the_csrf_header_on_changes(env, buoy):
    r = env.client.post("/auth/login", json={"username": "reviewer", "password": "reviewer123"})
    assert r.status_code == 200 and "scats_session" in env.client.cookies
    assert env.client.get("/datasets").status_code == 200                                              # reads work with the cookie
    assert env.client.post(f"/datasets/{buoy}/review/start").status_code == 403                       # no header: refused
    assert status_of() == "qc_done"
    assert env.client.post(f"/datasets/{buoy}/review/start", headers={"X-Requested-With": "scats-ui"}).status_code == 200


def test_password_reset_revokes_sessions_and_new_user_rules(env, hdr):
    assert env.client.post("/admin/users", headers=hdr.admin, json={"username": "bob", "password": "short", "role": "viewer"}).status_code == 422
    assert env.client.post("/admin/users", headers=hdr.admin, json={"username": "Bad Name", "password": NEW_PASSWORD, "role": "viewer"}).status_code == 422
    add_user(env.client, hdr.admin, "bob", "viewer")
    assert env.client.post("/admin/users", headers=hdr.admin, json={"username": "bob", "password": NEW_PASSWORD, "role": "viewer"}).status_code == 409
    bob = login(env.client, "bob", NEW_PASSWORD)
    assert env.client.post("/admin/users/bob/reset-password", headers=hdr.admin, json={"password": "another-long-pass-2"}).status_code == 200
    assert env.client.get("/auth/me", headers=bob).status_code == 401
    login(env.client, "bob", "another-long-pass-2")
    assert env.client.post("/admin/users/admin/disable", headers=hdr.admin).status_code == 422          # not yourself
    assert env.client.post("/admin/users/admin/role", headers=hdr.admin, json={"role": "viewer"}).status_code == 422
    assert env.client.post("/admin/users/bob/role", headers=hdr.admin, json={"role": "reviewer"}).status_code == 200
    users = {u["username"]: u for u in env.client.get("/admin/users", headers=hdr.admin).json()["users"]}
    assert users["bob"]["role"] == "reviewer" and "password_hash" not in str(users)


def test_default_password_warning_goes_away_when_the_seed_passwords_change(env, hdr):
    assert env.client.get("/health").json()["default_dev_passwords_in_use"] is True
    assert env.client.get("/auth/info").json()["warning"]
    for u in ("reviewer", "viewer"):
        assert env.client.post(f"/admin/users/{u}/reset-password", headers=hdr.admin, json={"password": NEW_PASSWORD}).status_code == 200
    assert env.client.get("/health").json()["default_dev_passwords_in_use"] is True                  # admin still default
    sql("SELECT auth_admin_reset_password(%s, 'admin', 'admin-new-long-password')", (__import__("scats_auth").hash_token(hdr.admin["Authorization"][7:]),), url=db_stub.REVIEW_URL)
    assert env.client.get("/health").json()["default_dev_passwords_in_use"] is False
    assert env.client.get("/auth/info").json()["warning"] is None


# ================================================================= state machine ==
LEGAL = {"start": {"qc_done"}, "flags": {"in_review"}, "confirm": {"in_review"}, "approved": {"in_review"},
         "rejected": {"in_review"}, "needs_work": {"in_review"}, "publish": {"approved"}}
STATUSES = ("ingested", "qc_running", "qc_done", "in_review", "approved", "rejected", "published")


def action(env, h, key, name):
    c = [{"observation_id": 1, "variable": "wind_speed", "level_index": -1}]
    return {"start": lambda: post(env, h, f"/datasets/{key}/review/start"),
            "flags": lambda: post(env, h, f"/datasets/{key}/flags", {"cells": c, "flag": 3, "comment": "c"}),
            "confirm": lambda: post(env, h, f"/datasets/{key}/flags/confirm", {"cells": c, "comment": "c"}),
            "approved": lambda: post(env, h, f"/datasets/{key}/review", {"decision": "approved", "comment": "c", "accept_unreviewed_flag4": True}),
            "rejected": lambda: post(env, h, f"/datasets/{key}/review", {"decision": "rejected", "comment": "c"}),
            "needs_work": lambda: post(env, h, f"/datasets/{key}/review", {"decision": "needs_work", "comment": "c"}),
            "publish": lambda: post(env, h, f"/datasets/{key}/publish")}[name]()


@pytest.mark.parametrize("name", sorted(LEGAL))
@pytest.mark.parametrize("status", STATUSES)
def test_illegal_transitions_are_409_and_change_nothing(env, hdr, buoy, name, status):
    if status in LEGAL[name]:
        pytest.skip("legal combination: covered by the workflow tests")
    sql("UPDATE dataset SET status = %s", (status,), fetch=False)
    before = (human_rows(), sql("SELECT count(*) FROM review")[0][0], sql("SELECT count(*) FROM audit_log")[0][0])
    r = action(env, hdr.reviewer, buoy, name)
    assert r.status_code == 409, f"{name} in {status}: {r.status_code} {r.text[:150]}"
    assert status_of() == status
    assert (human_rows(), sql("SELECT count(*) FROM review")[0][0], sql("SELECT count(*) FROM audit_log")[0][0]) == before


# ========================================================== reading and changing ==
def test_list_and_detail_carry_the_phase_2_context(env, hdr, buoy):
    d = env.client.get("/datasets", headers=hdr.viewer).json()["datasets"][0]
    assert d["dataset_key"] == buoy and d["flags_bad"] == 5 and d["flags_probably_bad"] == 2 and d["flags_missing"] == 2
    assert env.client.get("/datasets?status=in_review", headers=hdr.viewer).json()["total"] == 0
    assert env.client.get("/datasets?status=bogus", headers=hdr.viewer).status_code == 422
    j = env.client.get(f"/datasets/{buoy}", headers=hdr.viewer).json()
    assert j["synthetic_status"] == "true" and j["qc_config_version"] and j["units_assumed_from_mapping"] is False
    assert any("spike test" in x for x in j["limits"]) and any("gridded" in x for x in j["limits"])
    assert j["unreviewed_flag4"] == 5 and j["store"] is None


def test_flag_list_filters_and_history(env, hdr, review):
    four = flagged(env.client, hdr.viewer, review, 4)
    assert len(four) == 5 and all(f["flag"] == 4 and f["history"] and f["set_by"] is None for f in four)
    assert {f["variable"] for f in flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")} == {"relative_humidity"}
    assert all(f["test_id"] == "global_range" for f in flagged(env.client, hdr.viewer, review, 4, test="global_range"))
    page = env.client.get(f"/datasets/{review}/flags", headers=hdr.viewer, params={"page_size": 2, "page": 2}).json()
    assert len(page["flags"]) == 2 and page["total"] == 9
    assert env.client.get(f"/datasets/{review}/flags", headers=hdr.viewer, params={"page_size": 201}).status_code == 422


def test_change_flag_writes_a_human_row_with_history_and_audit(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")[0]
    r = post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "sensor was fine"})
    assert r.status_code == 200 and r.json()["cells"][0]["from"] == 4
    row = sql("SELECT flag, test_id, set_by_user_id, comment FROM qc_flag WHERE set_by_user_id IS NOT NULL")
    assert row == [(1, "expert_review", 2, "sensor was fine")]
    now = [x for x in flagged(env.client, hdr.viewer, review, 1) if x["observation_id"] == f["observation_id"]][0]
    assert now["set_by"] == "reviewer" and now["automatic_flag"] == 4 and [h["flag"] for h in now["history"]] == [4, 1]
    audit = env.client.get(f"/datasets/{review}/audit", headers=hdr.reviewer).json()["events"]
    ev = [e for e in audit if e["action"] == "flag.change"][0]
    assert ev["username"] == "reviewer" and ev["comment"] == "sensor was fine" and ev["after_state"]["cells"][0]["to"] == 1


@pytest.mark.parametrize("comment", ["", "   ", "\n\t"])
def test_a_comment_is_required(env, hdr, review, comment):
    f = flagged(env.client, hdr.viewer, review, 4)[0]
    r = post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 3, "comment": comment})
    assert r.status_code == 422 and "comment" in r.text and human_rows() == 0


def test_bad_flag_batches_are_refused_whole(env, hdr, review):
    fs = flagged(env.client, hdr.viewer, review, 4)
    good, same = cell_of(fs[0]), cell_of(fs[1])
    cases = {
        "same value": ({"cells": [good, same], "flag": 4, "comment": "c"}, "already has flag 4"),
        "flag 9": ({"cells": [good], "flag": 9, "comment": "c"}, "flags"),
        "flag 5": ({"cells": [good], "flag": 5, "comment": "c"}, "flags"),
        "foreign observation": ({"cells": [{"observation_id": 99999, "variable": "wind_speed"}], "flag": 3, "comment": "c"}, "not in this dataset"),
        "unknown variable": ({"cells": [{**good, "variable": "nope"}], "flag": 3, "comment": "c"}, "unknown variable"),
        "bad level": ({"cells": [{**good, "level_index": 3}], "flag": 3, "comment": "c"}, "level_index must be -1"),
        "duplicate cell": ({"cells": [good, good], "flag": 3, "comment": "c"}, "twice"),
    }
    for name, (body, text) in cases.items():
        r = post(env, hdr.reviewer, f"/datasets/{review}/flags", body)
        assert r.status_code == 422 and text in r.text, (name, r.status_code, r.text[:200])
    assert human_rows() == 0


def test_a_missing_value_cannot_be_changed(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 9)[0]
    r = post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "c"})
    assert r.status_code == 422 and "missing" in r.text


def test_batch_cap_is_enforced_and_named(env, hdr, review):
    from review_api.policy import MAX_FLAG_BATCH
    cells = [{"observation_id": 1, "variable": "wind_speed", "level_index": -1}] * (MAX_FLAG_BATCH + 1)
    r = post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": cells, "flag": 3, "comment": "c"})
    assert r.status_code == 422 and human_rows() == 0
    assert env.client.get(f"/datasets/{review}/cells/1", headers=hdr.viewer, params={"window": 99}).status_code == 422


def test_confirm_marks_a_cell_as_looked_at_but_not_twice(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 4)[0]
    r = post(env, hdr.reviewer, f"/datasets/{review}/flags/confirm", {"cells": [cell_of(f)], "comment": "real error"})
    assert r.status_code == 200
    assert sql("SELECT flag, comment FROM qc_flag WHERE set_by_user_id IS NOT NULL") == [(4, "real error")]
    again = post(env, hdr.reviewer, f"/datasets/{review}/flags/confirm", {"cells": [cell_of(f)], "comment": "again"})
    assert again.status_code == 422 and "already decided" in again.text
    assert env.client.get(f"/datasets/{review}", headers=hdr.viewer).json()["unreviewed_flag4"] == 4


def test_cell_view_shows_neighbours_and_effective_flags(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")[0]
    post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 2, "comment": "c"})
    j = env.client.get(f"/datasets/{review}/cells/{f['observation_id']}", headers=hdr.viewer, params={"variable": "relative_humidity", "window": 3}).json()
    s = j["series"][0]
    assert s["variable"] == "relative_humidity" and len(s["value"]) <= 7 and s["centre_record"] in s["record"]
    i = s["record"].index(s["centre_record"])
    assert s["staging_flag"][i] == 4 and s["effective_flag"][i] == 2
    assert j["observation"]["platform_id"] and j["flags"][0]["history"]
    assert env.client.get(f"/datasets/{review}/cells/987654", headers=hdr.viewer).status_code == 404
    assert env.client.get(f"/datasets/{review}/cells/1", headers=hdr.viewer, params={"variable": "nope"}).status_code == 422


def test_profile_cells_use_levels(env, hdr):
    ingest(CTD, env.staging)
    to_in_review(env.client, hdr.reviewer, CTD_KEY)
    f = flagged(env.client, hdr.viewer, CTD_KEY, 3, variable="temperature")[0]
    assert f["level_index"] >= 0
    ok = post(env, hdr.reviewer, f"/datasets/{CTD_KEY}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "c"})
    assert ok.status_code == 200
    bad = post(env, hdr.reviewer, f"/datasets/{CTD_KEY}/flags", {"cells": [{**cell_of(f), "level_index": 100000}], "flag": 1, "comment": "c"})
    assert bad.status_code == 422 and "outside this profile" in bad.text
    nolevel = post(env, hdr.reviewer, f"/datasets/{CTD_KEY}/flags", {"cells": [{**cell_of(f), "level_index": -1}], "flag": 1, "comment": "c"})
    assert nolevel.status_code == 422
    v = env.client.get(f"/datasets/{CTD_KEY}/cells/{f['observation_id']}", headers=hdr.viewer,
                       params={"variable": "temperature", "level_index": f["level_index"], "window": 2}).json()["series"][0]
    assert "depth_m" in v and v["effective_flag"][v["level_index"].index(f["level_index"])] == 1


def test_gridded_datasets_can_be_confirmed_but_not_changed(env, hdr):
    ingest(RAW_SST := __import__("review_helpers").RAW / "sst_grid/sst_synthetic_0p25deg_2025Q1.nc", env.staging)
    key = "sst_synthetic_0p25deg_2025q1"
    to_in_review(env.client, hdr.reviewer, key)
    rows = flagged(env.client, hdr.viewer, key, 3) or flagged(env.client, hdr.viewer, key, 4)
    if rows:
        assert post(env, hdr.reviewer, f"/datasets/{key}/flags", {"cells": [cell_of(rows[0])], "flag": 1, "comment": "c"}).status_code == 422
        assert post(env, hdr.reviewer, f"/datasets/{key}/flags/confirm", {"cells": [cell_of(rows[0])], "comment": "c"}).status_code == 200
    assert env.client.get(f"/datasets/{key}/cells/1", headers=hdr.viewer).status_code == 422
    assert RAW_SST.exists()


# ================================================================ decisions ==
def test_unreviewed_flag4_blocks_approval_unless_accepted_and_recorded(env, hdr, review):
    r = post(env, hdr.reviewer, f"/datasets/{review}/review", {"decision": "approved", "comment": "c"})
    assert r.status_code == 409 and r.json()["detail"]["unreviewed_flag4"] == 5 and status_of() == "in_review"
    r = approve(env, hdr.reviewer, review)
    assert r.status_code == 200 and r.json()["unreviewed_flag4_accepted"] == 5
    assert sql("SELECT accepted_unreviewed_flag4, unreviewed_flag4, decision FROM review") == [(True, 5, "approved")]
    ev = [e for e in env.client.get(f"/datasets/{review}/audit", headers=hdr.reviewer).json()["events"] if e["action"] == "dataset.approve"][0]
    assert ev["after_state"]["accepted_unreviewed_flag4"] is True and ev["after_state"]["unreviewed_flag4"] == 5


def test_approval_without_unreviewed_flags_needs_no_acceptance(env, hdr, review):
    for f in flagged(env.client, hdr.viewer, review, 4):
        post(env, hdr.reviewer, f"/datasets/{review}/flags/confirm", {"cells": [cell_of(f)], "comment": "seen"})
    r = post(env, hdr.reviewer, f"/datasets/{review}/review", {"decision": "approved", "comment": "all looked at"})
    assert r.status_code == 200 and r.json()["unreviewed_flag4_accepted"] == 0 and status_of() == "published"


def test_needs_work_keeps_the_dataset_in_review_and_reject_is_final(env, hdr, review):
    assert post(env, hdr.reviewer, f"/datasets/{review}/review", {"decision": "needs_work", "comment": "recheck the buoy"}).status_code == 200
    assert status_of() == "in_review" and sql("SELECT decision FROM review") == [("needs_work",)]
    assert post(env, hdr.reviewer, f"/datasets/{review}/review", {"decision": "rejected", "comment": "bad file"}).status_code == 200
    assert status_of() == "rejected"
    assert post(env, hdr.reviewer, f"/datasets/{review}/publish").status_code == 409
    assert post(env, hdr.reviewer, f"/datasets/{review}/review/start").status_code == 409
    assert env.client.get(f"/datasets/{review}", headers=hdr.viewer).json()["store"] is None
    import boto3
    assert boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="standardised").get("KeyCount") == 0   # rejected: nothing written


def test_decisions_need_a_comment(env, hdr, review):
    r = post(env, hdr.reviewer, f"/datasets/{review}/review", {"decision": "rejected", "comment": " "})
    assert r.status_code == 422 and status_of() == "in_review"


def test_four_eyes_on_blocks_the_ingester_and_off_allows_it(env, hdr):
    ingest(BUOY, env.staging, actor="reviewer")                         # the reviewer ingested it
    to_in_review(env.client, hdr.reviewer, BUOY_KEY)
    r = approve(env, hdr.reviewer, BUOY_KEY)
    assert r.status_code == 403 and "four-eyes" in r.text and status_of() == "in_review"
    assert approve(env, hdr.admin, BUOY_KEY).status_code == 200          # someone else may
    # off: same situation, rule disabled
    db_stub.reset_data()
    env.monkeypatch.setenv("REVIEW_FOUR_EYES", "false")
    h = login(env.client, "reviewer")
    ingest(BUOY, env.staging, actor="reviewer")
    to_in_review(env.client, h, BUOY_KEY)
    assert approve(env, h, BUOY_KEY).status_code == 200


# ================================================================ publish to Zarr ==
def test_full_workflow_publishes_zarr_with_the_reviewers_decisions(env, hdr, review):
    from scats_store import S3Store, read_store, verify_store_consistency
    f = flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")[0]
    post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "fine"})
    r = approve(env, hdr.admin, review)
    j = r.json()
    assert r.status_code == 200 and j["publish"]["ok"] and j["publish"]["consistency_ok"] and status_of() == "published"
    d = env.client.get(f"/datasets/{review}", headers=hdr.viewer).json()
    assert d["store"]["store_uri"] == f"s3://standardised/moored_buoy/{review}.zarr" and d["store"]["zarr_format"] == 2
    assert d["store"]["review_version"] == j["review_version"] and d["store"]["staging_superseded_at"]
    s3 = S3Store.from_env()
    z = read_store(s3, "standardised", f"moored_buoy/{review}.zarr", expected_manifest_sha256=d["store"]["manifest_sha256"])
    rec = sql("SELECT source_record FROM observation WHERE observation_id = %s", (f["observation_id"],))[0][0]
    assert int(z["relative_humidity_qc"].values[rec]) == 1                                           # the decision is in the file
    st = __import__("scats_ingest.staging", fromlist=["x"]).read_staging(env.staging, review)
    assert int(st["relative_humidity_qc"].values[rec]) == 4                                           # staging is untouched
    assert np.array_equal(z["relative_humidity"].values, st["relative_humidity"].values, equal_nan=True)   # values never edited
    assert z.attrs["scats_reviewer"] == "admin" and z.attrs["scats_review_version"] == j["review_version"] and z.attrs["scats_qc_config_version"]
    assert verify_store_consistency(review, db_url=db_stub.REVIEW_URL, staging_dir=env.staging, s3=s3).ok
    assert sql("SELECT audit_verify_chain()") == [(None,)]
    assert env.client.get("/audit/verify", headers=hdr.admin).json() == {"ok": True, "first_broken_audit_id": None, "rows_checked": sql("SELECT count(*) FROM audit_log")[0][0]}
    assert post(env, hdr.reviewer, f"/datasets/{review}/publish").status_code == 409                # already published


def test_profile_dataset_publishes_with_level_overrides(env, hdr):
    from scats_store import S3Store, read_store
    ingest(CTD, env.staging)
    to_in_review(env.client, hdr.reviewer, CTD_KEY)
    f = flagged(env.client, hdr.viewer, CTD_KEY, 3, variable="temperature")[0]
    post(env, hdr.reviewer, f"/datasets/{CTD_KEY}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "c"})
    j = approve(env, hdr.admin, CTD_KEY).json()
    assert j["publish"]["ok"] and j["publish"]["consistency_ok"]
    z = read_store(S3Store.from_env(), "standardised", f"ship_ctd/{CTD_KEY}.zarr")
    rec = sql("SELECT source_record FROM observation WHERE observation_id = %s", (f["observation_id"],))[0][0]
    assert int(z["temperature_qc"].values[rec, f["level_index"]]) == 1


def test_zarr_failure_leaves_the_catalogue_and_the_store_as_they_were_then_retry_works(env, hdr, review):
    import boto3
    from scats_store import S3Store
    calls = {"n": 0}
    real = S3Store.put_file

    def flaky(self, bucket, key, path):
        calls["n"] += 1
        if calls["n"] == 5:
            raise ConnectionError("storage went away")
        return real(self, bucket, key, path)

    env.monkeypatch.setattr(S3Store, "put_file", flaky)
    j = approve(env, hdr.admin, review).json()
    assert j["status"] == "approved" and j["publish"]["ok"] is False and "nothing published" in str(j["publish"]["error"])
    assert status_of() == "approved" and sql("SELECT count(*) FROM dataset_store") == [(0,)]
    assert boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="standardised").get("KeyCount") == 0
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'dataset.publish_failed'") == [(1,)]
    env.monkeypatch.setattr(S3Store, "put_file", real)
    r = post(env, hdr.reviewer, f"/datasets/{review}/publish")
    assert r.status_code == 200 and status_of() == "published" and r.json()["consistency_ok"]


def test_failure_during_the_copy_removes_the_partial_final_prefix(env, hdr, review):
    import boto3
    from scats_store import S3Store
    n = {"c": 0}
    real = S3Store.copy

    def flaky(self, bucket, src, dst):
        n["c"] += 1
        if n["c"] == 4:
            raise ConnectionError("died mid-copy")
        return real(self, bucket, src, dst)

    env.monkeypatch.setattr(S3Store, "copy", flaky)
    j = approve(env, hdr.admin, review).json()
    assert j["publish"]["ok"] is False and "partial copy removed" in str(j["publish"]["error"])
    assert boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="standardised").get("KeyCount") == 0
    assert status_of() == "approved"


def test_a_corrupted_upload_is_caught_before_anything_is_published(env, hdr, review):
    from scats_store import S3Store
    env.monkeypatch.setattr(S3Store, "sha256", lambda self, b, k: ("0" * 64, 1))
    j = approve(env, hdr.admin, review).json()
    assert j["publish"]["ok"] is False and "differs after upload" in str(j["publish"]["error"]) and status_of() == "approved"


def test_catalogue_failure_after_the_zarr_write_is_reported_and_retry_is_safe(env, hdr, review):
    from review_api import service
    real = service.audit
    state = {"fail": True}

    def audit_fails_once(cur, principal, action, *a, **k):
        if action == "dataset.publish" and state["fail"]:
            raise RuntimeError("audit insert failed")
        return real(cur, principal, action, *a, **k)

    env.monkeypatch.setattr(service, "audit", audit_fails_once)
    j = approve(env, hdr.admin, review).json()
    assert j["publish"]["ok"] is False and "catalogue update failed" in str(j["publish"]["error"])
    assert status_of() == "approved" and sql("SELECT count(*) FROM dataset_store") == [(0,)]          # rolled back with its audit row
    state["fail"] = False
    assert post(env, hdr.reviewer, f"/datasets/{review}/publish").status_code == 200 and status_of() == "published"


def test_tampering_with_a_published_object_is_detected(env, hdr, review):
    import boto3
    from scats_store import S3Store, StoreCorrupt, read_store, verify_store_consistency
    assert approve(env, hdr.admin, review).json()["publish"]["ok"]
    s3c = boto3.client("s3", region_name="us-east-1")
    key = f"moored_buoy/{review}.zarr/relative_humidity/0"
    s3c.put_object(Bucket="standardised", Key=key, Body=b"tampered")
    with pytest.raises(StoreCorrupt):
        read_store(S3Store.from_env(), "standardised", f"moored_buoy/{review}.zarr")
    assert not verify_store_consistency(review, db_url=db_stub.REVIEW_URL, staging_dir=env.staging, s3=S3Store.from_env()).ok


def test_publish_uses_exactly_the_approved_snapshot(env, hdr, review):
    """A decision that appears AFTER the approval (here forced in with the guard off, as a race would) must not reach the Zarr."""
    from scats_store import S3Store, read_store
    env.monkeypatch.setenv("PUBLISH_ON_APPROVAL", "false")
    f = flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")[0]
    j = approve(env, hdr.admin, review).json()
    sql("ALTER TABLE qc_flag DISABLE TRIGGER qc_flag_human_guard_trg", fetch=False)
    try:
        sql("INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id, set_by_user_id, comment)"
            " VALUES (%s, 'relative_humidity', -1, 1, 'expert_review', 2, 'late decision')", (f["observation_id"],), fetch=False)
    finally:
        sql("ALTER TABLE qc_flag ENABLE TRIGGER qc_flag_human_guard_trg", fetch=False)
    assert sql("SELECT max(qc_flag_id) FROM qc_flag")[0][0] > j["review_version"]
    r = post(env, hdr.reviewer, f"/datasets/{review}/publish")
    assert r.status_code == 200 and r.json()["consistency_ok"] and r.json()["review_version"] == j["review_version"]
    z = read_store(S3Store.from_env(), "standardised", f"moored_buoy/{review}.zarr")
    rec = sql("SELECT source_record FROM observation WHERE observation_id = %s", (f["observation_id"],))[0][0]
    assert int(z["relative_humidity_qc"].values[rec]) == 4                                            # the late 1 is NOT in the file


# ================================================================ concurrency ==
def test_two_reviewers_on_one_cell_keep_both_rows_and_the_later_wins(env, hdr, review):
    add_user(env.client, hdr.admin, "carol", "reviewer")
    carol = login(env.client, "carol", NEW_PASSWORD)
    f = flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")[0]
    assert post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "first"}).status_code == 200
    assert post(env, carol, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 3, "comment": "second"}).status_code == 200
    now = [x for x in flagged(env.client, hdr.viewer, review, 3) if x["observation_id"] == f["observation_id"]][0]
    assert now["set_by"] == "carol" and [(h["flag"], h["set_by"]) for h in now["history"]] == [(4, None), (1, "reviewer"), (3, "carol")]
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'flag.change'") == [(2,)]


def test_an_approval_waits_for_a_change_in_progress_and_includes_it(env, hdr, review):
    """Reviewer B holds an open change (shared lock); the approval must wait for it and then see it."""
    import psycopg2
    from scats_store import S3Store, read_store
    f = flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")[0]
    con = psycopg2.connect(db_stub.REVIEW_URL)
    cur = con.cursor()
    cur.execute("SELECT status FROM dataset WHERE dataset_key = %s FOR SHARE", (review,))
    cur.execute("INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id, set_by_user_id, comment)"
                " VALUES (%s, 'relative_humidity', -1, 2, 'expert_review', 2, 'in progress') RETURNING qc_flag_id", (f["observation_id"],))
    pending_id = cur.fetchone()[0]
    result = {}
    t = threading.Thread(target=lambda: result.update(r=approve(env, hdr.admin, review)))
    t.start()
    time.sleep(1.5)
    assert t.is_alive(), "the approval did not wait for the change in progress"
    con.commit()
    con.close()
    t.join(20)
    j = result["r"].json()
    assert j["review_version"] >= pending_id
    z = read_store(S3Store.from_env(), "standardised", f"moored_buoy/{review}.zarr")
    rec = sql("SELECT source_record FROM observation WHERE observation_id = %s", (f["observation_id"],))[0][0]
    assert int(z["relative_humidity_qc"].values[rec]) == 2


def test_a_change_after_the_approval_is_refused(env, hdr, review):
    env.monkeypatch.setenv("PUBLISH_ON_APPROVAL", "false")
    f = flagged(env.client, hdr.viewer, review, 4)[0]
    approve(env, hdr.admin, review)
    r = post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "too late"})
    assert r.status_code == 409 and human_rows() == 0


# ============================================================ audit atomicity ==
def test_a_failing_audit_row_undoes_the_flag_change(env, hdr, review):
    from review_api import service
    f = flagged(env.client, hdr.viewer, review, 4)[0]
    env.monkeypatch.setattr(service, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    r = post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "c"})
    assert r.status_code == 500 and human_rows() == 0


def test_a_failing_audit_row_undoes_the_status_change(env, hdr, buoy):
    from review_api import service
    env.monkeypatch.setattr(service, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    assert post(env, hdr.reviewer, f"/datasets/{buoy}/review/start").status_code == 500 and status_of() == "qc_done"
    env.monkeypatch.undo()
    set_env(env.monkeypatch, env.staging)
    to_in_review(env.client, login(env.client, "reviewer"), buoy)
    env.monkeypatch.setattr(service, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    r = post(env, login(env.client, "reviewer"), f"/datasets/{buoy}/review", {"decision": "rejected", "comment": "c"})
    assert r.status_code == 500 and status_of() == "in_review" and sql("SELECT count(*) FROM review") == [(0,)]


def test_audit_chain_breaks_when_a_row_is_edited_and_admin_sees_it(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 4)[0]
    post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "c"})
    assert env.client.get("/audit/verify", headers=hdr.admin).json()["ok"] is True
    sql("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete", fetch=False)
    sql("UPDATE audit_log SET username = 'someone-else' WHERE action = 'flag.change'", fetch=False)
    sql("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete", fetch=False)
    j = env.client.get("/audit/verify", headers=hdr.admin).json()
    assert j["ok"] is False and j["first_broken_audit_id"]
    assert env.client.get("/health").json()["audit_chain_ok_recent"] is False


def test_viewers_do_not_see_audit_states_but_reviewers_do(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 4)[0]
    post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "c"})
    v = env.client.get(f"/datasets/{review}/audit", headers=hdr.viewer).json()["events"]
    r = env.client.get(f"/datasets/{review}/audit", headers=hdr.reviewer).json()["events"]
    assert v and all("before_state" not in e and "after_state" not in e for e in v)
    assert any(e.get("after_state") for e in r)
    assert "row_hash" not in str(v) and "row_hash" not in str(r) and "prev_hash" not in str(r)


# ======================================================================= safety ==
SCRIPT = "<script>alert('xss')</script><img src=x onerror=alert(1)>"


def test_hostile_text_is_stored_and_returned_as_data_only(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 4)[0]
    assert post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": SCRIPT}).status_code == 200
    add_user(env.client, hdr.admin, "x-script.user", "viewer")
    for path in (f"/datasets/{review}/audit", f"/datasets/{review}/flags?flag=1"):
        r = env.client.get(path, headers=hdr.viewer)
        assert r.headers["content-type"].startswith("application/json") and r.headers["x-content-type-options"] == "nosniff"
        assert SCRIPT in str(r.json())                                   # kept exactly: it is escaped when drawn, by the page
    page = env.client.get("/")
    assert "script-src 'self'" in page.headers["content-security-policy"] and "unsafe-inline" not in page.headers["content-security-policy"]
    assert "<script>" not in page.text.replace('<script src="/static/app.js"></script>', "")


def test_the_page_script_never_builds_markup_from_strings():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "components/04_expert_review_ui/static/app.js").read_text()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage", "sessionStorage"):
        assert banned not in js, banned
    assert "textContent" in js or "createTextNode" in js


@pytest.mark.parametrize("param,value", [("variable", "x' OR '1'='1"), ("variable", "wind_speed'; DROP TABLE qc_flag; --"),
                                          ("test", "global_range' UNION SELECT password_hash FROM app_user --"),
                                          ("test", "%"), ("variable", "%' OR 1=1 --")])
def test_injection_in_filters_returns_nothing(env, hdr, review, param, value):
    r = env.client.get(f"/datasets/{review}/flags", headers=hdr.viewer, params={param: value})
    assert r.status_code == 200 and r.json()["total"] == 0 and r.json()["flags"] == []
    assert sql("SELECT to_regclass('qc_flag')") == [("qc_flag",)]


@pytest.mark.parametrize("path", ["/datasets/x'%20OR%20'1'='1", "/datasets/%27%3B%20DROP%20TABLE%20dataset%3B--/flags",
                                  "/datasets?status=qc_done'%20OR%20'1'='1", f"/datasets/{BUOY_KEY}/flags?flag=1;DROP%20TABLE%20qc_flag",
                                  f"/datasets/{BUOY_KEY}/flags?page=-1", f"/datasets/{BUOY_KEY}/cells/1%20OR%201=1"])
def test_injection_in_paths_and_numbers_is_a_4xx(env, hdr, buoy, path):
    r = env.client.get(path, headers=hdr.viewer)
    assert 400 <= r.status_code < 500 and "password_hash" not in r.text
    assert sql("SELECT to_regclass('dataset')") == [("dataset",)]


# ================================================== re-QC, cleared rows, re-ingest ==
def test_an_override_survives_a_qc_rerun(env, hdr, review):
    f = flagged(env.client, hdr.viewer, review, 4, variable="relative_humidity")[0]
    post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 1, "comment": "fine"})
    r = ingest(BUOY, env.staging)                                       # same file, QC runs again
    assert r.reingest and r.status == "in_review" and status_of() == "in_review"
    now = [x for x in flagged(env.client, hdr.viewer, review, 1) if x["observation_id"] == f["observation_id"]][0]
    assert now["set_by"] == "reviewer" and now["flag"] == 1


def test_a_cell_that_stops_being_flagged_is_cleared_and_overrides_still_win(env, hdr, review):
    from scats_ingest import verify_flag_consistency
    from scats_qc import ALL_TESTS
    before = flagged(env.client, hdr.viewer, review, 4)
    f = [x for x in before if x["test_id"] == "position_on_land"][0]
    post(env, hdr.reviewer, f"/datasets/{review}/flags", {"cells": [cell_of(f)], "flag": 3, "comment": "keep as probably bad"})
    r = ingest(BUOY, env.staging, tests=[t() for t in ALL_TESTS if t.test_id != "position_on_land"])   # config change: that test is off
    assert r.flag_rows_cleared >= 1
    after = flagged(env.client, hdr.viewer, review, 4)
    assert not [x for x in after if x["test_id"] == "position_on_land"]                              # cleared, no stale flag 4
    mine = [x for x in flagged(env.client, hdr.viewer, review, 3) if x["observation_id"] == f["observation_id"]][0]
    assert mine["set_by"] == "reviewer" and mine["automatic_flag"] in (0, 1)                          # the person's decision stays
    assert [h["flag"] for h in mine["history"]][:2] == [4, 1] or mine["history"][0]["flag"] == 4        # old row kept as history
    assert verify_flag_consistency(review, db_url=db_stub.INGEST_URL, staging_dir=env.staging).ok


def test_reviewed_datasets_refuse_a_reingest(env, hdr, review):
    from scats_ingest import IngestConflict
    approve(env, hdr.admin, review)
    with pytest.raises(IngestConflict):
        ingest(BUOY, env.staging)
    assert status_of() == "published"


# ============================================================ parser API tokens ==
@pytest.fixture
def parser(env, monkeypatch):
    """The parser API runs as scats_ingest, the review API as scats_review; both read DATABASE_URL, so switch it per call."""
    import importlib

    from review_helpers import ROOT
    monkeypatch.setenv("INGEST_ALLOWED_DIRS", str(ROOT / "data"))
    monkeypatch.setenv("INGEST_API_KEY", "")
    import app.main as m
    importlib.reload(m)
    inner = TestClient(m.app)

    class AsIngest:
        def __getattr__(self, name):
            def call(*a, **k):
                monkeypatch.setenv("DATABASE_URL", db_stub.INGEST_URL)
                try:
                    return getattr(inner, name)(*a, **k)
                finally:
                    monkeypatch.setenv("DATABASE_URL", db_stub.REVIEW_URL)
            return call
    yield AsIngest()


def test_ingest_with_an_admin_token_is_audited_under_the_admin_and_four_eyes_follows(env, hdr, parser):
    body = {"path": str(BUOY)}
    assert parser.post("/ingest", json=body).status_code == 503                                         # no key, no token
    assert parser.post("/ingest", json=body, headers=hdr.reviewer).status_code == 403                   # not an admin
    assert parser.post("/ingest", json=body, headers={"Authorization": "Bearer nonsense"}).status_code == 401
    r = parser.post("/ingest", json=body, headers=hdr.admin)
    assert r.status_code == 200 and r.json()["flag_consistency_ok"]
    assert sql("SELECT username, user_id FROM audit_log WHERE action = 'dataset.ingest'") == [("admin", 1)]
    to_in_review(env.client, hdr.reviewer, BUOY_KEY)
    assert approve(env, hdr.admin, BUOY_KEY).status_code == 403                                         # the ingesting admin cannot approve
    assert approve(env, hdr.reviewer, BUOY_KEY).status_code == 200


def test_parser_api_reads_with_any_valid_token_and_still_honours_the_service_key(env, hdr, parser, monkeypatch):
    parser.post("/ingest", json={"path": str(BUOY)}, headers=hdr.admin)
    assert parser.get(f"/datasets/{BUOY_KEY}/qc-summary", headers=hdr.viewer).status_code == 200
    assert parser.get(f"/datasets/{BUOY_KEY}/qc-summary", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    monkeypatch.setenv("INGEST_API_KEY", "svc-key")
    assert parser.post("/ingest", json={"path": str(BUOY)}, headers={"X-API-Key": "svc-key"}).status_code == 200
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'dataset.ingest' AND username = 'scats_ingest'") == [(1,)]
    assert parser.get(f"/datasets/{BUOY_KEY}/qc-summary").status_code == 401
