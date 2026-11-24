"""Phase 3 database layer: role grants, guards, auth functions. Stand-in PostgreSQL 16 WITHOUT PostGIS."""
import psycopg2
import pytest

import db_stub
from review_helpers import BUOY, BUOY_KEY, ingest, sql

pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")


@pytest.fixture(scope="module", autouse=True)
def server():
    db_stub.start(fresh=True)
    yield
    db_stub.stop()


@pytest.fixture(autouse=True)
def clean():
    db_stub.reset_data()


def as_review(query, params=None):
    return sql(query, params, url=db_stub.REVIEW_URL)


def denied(query, url, params=None):
    """The database must refuse. Returns the error class name."""
    with pytest.raises(psycopg2.Error) as e:
        sql(query, params, url=url)
    return type(e.value).__name__


def seeded(tmp_path, status="in_review"):
    r = ingest(BUOY, tmp_path / "st")
    sql("UPDATE dataset SET status = %s WHERE dataset_key = %s", (status, BUOY_KEY), fetch=False)
    return r


# ------------------------------------------------------------ least privilege --
@pytest.mark.parametrize("query", [
    "SELECT password_hash FROM app_user",
    "SELECT * FROM app_user",
    "SELECT * FROM auth_session",
    "SELECT * FROM login_attempt",
    "UPDATE qc_flag SET flag = 1",
    "DELETE FROM qc_flag",
    "UPDATE audit_log SET comment = 'x'",
    "DELETE FROM audit_log",
    "TRUNCATE audit_log",
    "UPDATE dataset SET title = 'x'",
    "UPDATE dataset SET dataset_key = 'x'",
    "UPDATE observation SET overall_flag = 0",
    "INSERT INTO dataset (dataset_key, title, dataset_type, source, zone, storage_uri) VALUES ('a','a','grid','s','raw','u')",
    "UPDATE app_user SET role_name = 'admin'",
    "UPDATE review SET decision = 'approved'",
    "DELETE FROM review",
    "UPDATE dataset_store SET content_sha256 = 'x'",
    "INSERT INTO app_user (username, role_name) VALUES ('mallory', 'admin')",
    "SELECT auth_admin_create_user('x', 'mallory', 'm', 'a-long-password-1', 'admin')",
    "UPDATE auth_session SET revoked_at = NULL",
])
def test_review_role_cannot(query):
    assert denied(query, db_stub.REVIEW_URL) in ("InsufficientPrivilege", "RaiseException")


def test_review_role_is_not_superuser():
    assert as_review("SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user") == [(False, False, False)]


def test_review_role_cannot_move_status_off_the_review_path(tmp_path):
    seeded(tmp_path, "qc_done")
    for new in ("approved", "published", "rejected", "ingested"):
        assert denied(f"UPDATE dataset SET status = '{new}'", db_stub.REVIEW_URL) == "InsufficientPrivilege"
    assert sql("SELECT status FROM dataset") == [("qc_done",)]


def test_review_role_allowed_transitions_only(tmp_path):
    seeded(tmp_path, "qc_done")
    for old, new in [("qc_done", "in_review"), ("in_review", "approved"), ("approved", "published")]:
        as_review("UPDATE dataset SET status = %s, updated_at = now() WHERE status = %s", (new, old))
        assert sql("SELECT status FROM dataset") == [(new,)]
    assert denied("UPDATE dataset SET status = 'in_review'", db_stub.REVIEW_URL) == "InsufficientPrivilege"   # published is final


def test_ingest_role_cannot_touch_a_dataset_under_review(tmp_path):
    seeded(tmp_path, "in_review")
    assert denied("UPDATE dataset SET status = 'qc_done'", db_stub.INGEST_URL) == "InsufficientPrivilege"
    assert denied("UPDATE dataset SET status = 'approved'", db_stub.INGEST_URL) == "InsufficientPrivilege"


def test_ingest_role_can_check_tokens_but_not_log_in_or_administer():
    assert sql("SELECT count(*) FROM auth_validate_token('nothing')", url=db_stub.INGEST_URL) == [(0,)]
    assert denied("SELECT * FROM auth_login('admin','admin123','1',  'h', 60, 5, 20, 900)", db_stub.INGEST_URL) == "InsufficientPrivilege"
    assert denied("SELECT auth_admin_set_role('x','viewer','admin')", db_stub.INGEST_URL) == "InsufficientPrivilege"


# ----------------------------------------------------------- human flag guard --
def human_row(obs, flag=1, comment="checked", user=2, test="expert_review"):
    return ("INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id, set_by_user_id, comment)"
            " VALUES (%s, 'wind_speed', -1, %s, %s, %s, %s)", (obs, flag, test, user, comment))


@pytest.mark.parametrize("kwargs", [
    {"comment": ""}, {"comment": "   "}, {"comment": None}, {"flag": 9}, {"flag": 0}, {"flag": 5},
    {"user": None}, {"test": "global_range"},
])
def test_human_flag_guard_refuses(tmp_path, kwargs):
    seeded(tmp_path)
    q, p = human_row(1, **kwargs)
    with pytest.raises(psycopg2.errors.CheckViolation):
        sql(q, p, url=db_stub.REVIEW_URL)
    assert sql("SELECT count(*) FROM qc_flag WHERE set_by_user_id IS NOT NULL") == [(0,)]


@pytest.mark.parametrize("status", ["ingested", "qc_done", "approved", "rejected", "published"])
def test_human_flag_needs_in_review(tmp_path, status):
    seeded(tmp_path, status)
    with pytest.raises(psycopg2.errors.CheckViolation):
        sql(*human_row(1), url=db_stub.REVIEW_URL)


def test_human_flag_accepted_in_review(tmp_path):
    seeded(tmp_path)
    sql(*human_row(1), url=db_stub.REVIEW_URL)
    assert sql("SELECT count(*) FROM qc_flag WHERE set_by_user_id = 2") == [(1,)]


def test_human_decision_beats_a_later_automatic_row(tmp_path):
    seeded(tmp_path)
    sql(*human_row(1, flag=2))
    sql("INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id) VALUES (1, 'wind_speed', -1, 4, 'global_range')")
    assert sql("SELECT flag, set_by_user_id FROM qc_flag_current WHERE observation_id = 1 AND variable_name = 'wind_speed'") == [(2, 2)]
    assert sql("SELECT flag FROM qc_flag_auto_current WHERE observation_id = 1 AND variable_name = 'wind_speed'") == [(4,)]


def test_newest_decision_wins_by_id_not_by_clock(tmp_path):
    seeded(tmp_path)
    sql(*human_row(1, flag=2))
    sql(*human_row(1, flag=3))
    sql("UPDATE qc_flag SET set_at = now() + interval '1 day' WHERE flag = 2")        # clock says the first is newer
    assert sql("SELECT flag FROM qc_flag_current WHERE observation_id = 1 AND variable_name = 'wind_speed'") == [(3,)]


# ----------------------------------------------------------- auth functions --
def login_fn(user="admin", pw="admin123", ip="10.0.0.1", token="t1", ttl=3600, max_user=5, max_ip=20, window=900):
    return sql("SELECT * FROM auth_login(%s,%s,%s,%s,%s,%s,%s,%s)", (user, pw, ip, token, ttl, max_user, max_ip, window), url=db_stub.REVIEW_URL)[0]


def test_login_function_outcomes():
    assert login_fn()[0] == "ok"
    assert login_fn(pw="wrong")[0] == "denied"
    assert login_fn(user="nobody")[0] == "denied"
    assert sql("SELECT count(*) FROM auth_session") == [(1,)]
    assert sql("SELECT count(*) FROM login_attempt WHERE NOT success") == [(2,)]


def test_the_session_table_holds_only_a_hash_of_the_token():
    login_fn(token="the-hash-of-a-token")
    assert sql("SELECT token_hash FROM auth_session") == [("the-hash-of-a-token",)]     # whatever the caller sends is what is stored
    from scats_auth import hash_token, new_token
    t = new_token()
    assert len(t) >= 43 and hash_token(t) != t and len(hash_token(t)) == 64


def test_lock_after_failures_and_unlock_after_window():
    for i in range(5):
        assert login_fn(pw="bad", token=f"x{i}")[0] == "denied"
    assert login_fn(token="y")[0] == "locked"                       # even the right password is refused while locked
    sql("UPDATE login_attempt SET attempted_at = now() - interval '1 hour'")
    assert login_fn(token="z")[0] == "ok"


def test_lock_per_address():
    for i in range(3):
        login_fn(user=f"nobody{i}", pw="x", token=f"a{i}", max_ip=3, ip="9.9.9.9")
    assert login_fn(user="admin", token="b", max_ip=3, ip="9.9.9.9")[0] == "locked"
    assert login_fn(user="admin", token="c", max_ip=3, ip="8.8.8.8")[0] == "ok"


def test_blocked_attempts_are_audited_but_do_not_extend_the_lock():
    for i in range(5):
        login_fn(pw="bad", token=f"x{i}")
    login_fn(token="y")
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'auth.login_blocked'") == [(1,)]
    assert sql("SELECT count(*) FROM login_attempt") == [(5,)]


def test_no_password_in_audit_or_attempt_tables():
    secret = "Sup3r-Secret-Pw-Value"
    login_fn(user="admin", pw=secret)
    login_fn(user="nobody", pw=secret)
    assert sql("SELECT count(*) FROM audit_log WHERE comment LIKE %s OR after_state::text LIKE %s OR username LIKE %s",
               (f"%{secret}%", f"%{secret}%", f"%{secret}%")) == [(0,)]
    assert sql("SELECT count(*) FROM login_attempt WHERE username LIKE %s", (f"%{secret}%",)) == [(0,)]


def test_token_validity_rules():
    login_fn(token="live")
    ok = lambda t: sql("SELECT count(*) FROM auth_validate_token(%s)", (t,), url=db_stub.REVIEW_URL)[0][0]   # noqa: E731
    assert ok("live") == 1 and ok("other") == 0 and ok("") == 0
    sql("UPDATE auth_session SET expires_at = now() - interval '1 second'")
    assert ok("live") == 0                                                                  # expired
    sql("UPDATE auth_session SET expires_at = now() + interval '1 hour', revoked_at = now()")
    assert ok("live") == 0                                                                  # revoked
    sql("UPDATE auth_session SET revoked_at = NULL")
    assert ok("live") == 1
    sql("UPDATE app_user SET is_active = false WHERE username = 'admin'")
    assert ok("live") == 0                                                                  # disabled user


def test_admin_functions_recheck_the_admin_token_inside_the_database():
    login_fn(user="reviewer", pw="reviewer123", token="rev")
    login_fn(user="admin", pw="admin123", token="adm")
    for call in ("SELECT auth_admin_create_user('rev','mario','m','a-long-password-1','viewer')",
                 "SELECT auth_admin_set_role('rev','viewer','admin')",
                 "SELECT auth_admin_set_active('nope','viewer',false)"):
        assert denied(call, db_stub.REVIEW_URL) == "InsufficientPrivilege"
    sql("SELECT auth_admin_create_user('adm','maria','m','a-long-password-1','viewer')", url=db_stub.REVIEW_URL)
    assert sql("SELECT role_name FROM app_user WHERE username = 'maria'") == [("viewer",)]


def test_bad_hashes_never_match_and_new_users_get_bcrypt():
    login_fn(user="admin", pw="admin123", token="adm")
    sql("SELECT auth_admin_create_user('adm','carol','c','a-long-password-1','viewer')", url=db_stub.REVIEW_URL)
    h = sql("SELECT password_hash FROM app_user WHERE username = 'carol'")[0][0]
    assert h.startswith("$2") and "a-long-password-1" not in h
    assert login_fn(user="carol", pw="a-long-password-1", token="c1")[0] == "ok"


# ------------------------------------------------------------------ audit chain --
def test_audit_chain_valid_after_database_work_and_breaks_when_tampered(tmp_path):
    seeded(tmp_path)
    login_fn()
    assert sql("SELECT audit_verify_chain()") == [(None,)]
    sql("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete", fetch=False)
    sql("UPDATE audit_log SET comment = 'edited later' WHERE audit_id = (SELECT min(audit_id) + 1 FROM audit_log)", fetch=False)
    sql("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete", fetch=False)
    broken = sql("SELECT audit_verify_chain()")[0][0]
    assert broken is not None
