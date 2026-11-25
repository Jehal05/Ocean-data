"""Migration 007 on the stand-in database: least privilege for the two new roles, append-only log, limits, stale marking."""
import psycopg2
import pytest

import db_stub
from review_helpers import sql

pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")


@pytest.fixture(scope="module", autouse=True)
def server():
    db_stub.start(fresh=True)
    yield
    db_stub.stop()


@pytest.fixture(autouse=True)
def clean():
    db_stub.reset_data()


def run(url, query, params=None):
    return sql(query, params, url=url)


PRODUCTS_DENIED = [
    "SELECT * FROM app_user", "SELECT * FROM auth_session", "SELECT * FROM login_attempt", "SELECT * FROM qc_flag", "SELECT * FROM observation",
    "SELECT * FROM review", "SELECT * FROM query_log", "SELECT * FROM api_limit",
    "INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id) VALUES (1,'t',0,3,'x')",
    "INSERT INTO dataset_store (dataset_id) VALUES (1)", "INSERT INTO query_log (user_id, username, role_name, action) VALUES (1,'x','viewer','discover')",
    "UPDATE dataset SET status = 'published'", "UPDATE dataset_store SET content_sha256 = 'x'",
    "UPDATE derived_product SET content_sha256 = 'x'", "UPDATE derived_product SET stale_at = now()", "DELETE FROM derived_product",
    "DELETE FROM audit_log", "UPDATE audit_log SET action = 'x'", "TRUNCATE derived_product",
    "SELECT auth_admin_create_user('x','y','z','w','viewer')", "SELECT * FROM auth_validate_token('x')",
]
ACCESS_DENIED = [
    "SELECT * FROM app_user", "SELECT * FROM auth_session", "SELECT * FROM login_attempt", "SELECT * FROM qc_flag", "SELECT * FROM observation",
    "SELECT * FROM review", "SELECT * FROM dataset", "SELECT * FROM dataset_store", "SELECT * FROM derived_product", "SELECT * FROM query_log",
    "INSERT INTO derived_product (kind) VALUES ('binned_profile')", "INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id) VALUES (1,'t',0,3,'x')",
    "INSERT INTO review (dataset_id) VALUES (1)", "INSERT INTO dataset (dataset_key) VALUES ('x')",
    "UPDATE api_limit SET value = 1", "DELETE FROM api_limit", "INSERT INTO api_limit (role_name, name, value) VALUES ('viewer','rate_per_minute',1)",
    "UPDATE query_log SET n_rows = 1", "DELETE FROM query_log", "TRUNCATE query_log", "UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log",
    "SELECT auth_admin_create_user('x','y','z','w','viewer')", "SELECT auth_admin_set_role('x','y','admin')",
]


@pytest.mark.parametrize("query", PRODUCTS_DENIED)
def test_the_products_role_cannot(query):
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(db_stub.PRODUCTS_URL, query)


@pytest.mark.parametrize("query", ACCESS_DENIED)
def test_the_access_role_cannot(query):
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(db_stub.ACCESS_URL, query)


def test_what_the_two_roles_can_do():
    assert run(db_stub.ACCESS_URL, "SELECT count(*) FROM catalogue_dataset")[0][0] == 0
    assert run(db_stub.ACCESS_URL, "SELECT count(*) FROM catalogue_product")[0][0] == 0
    assert run(db_stub.ACCESS_URL, "SELECT count(*) FROM api_limit")[0][0] == 24
    run(db_stub.ACCESS_URL, "INSERT INTO query_log (user_id, username, role_name, action) VALUES (3,'viewer','viewer','discover')")
    assert run(db_stub.ACCESS_URL, "SELECT access_recent_count(3, 60)")[0][0] == 1
    assert run(db_stub.PRODUCTS_URL, "SELECT count(*) FROM dataset_store")[0][0] == 0


def test_the_query_log_is_append_only_even_for_the_owner():
    run(db_stub.ACCESS_URL, "INSERT INTO query_log (user_id, username, role_name, action) VALUES (3,'viewer','viewer','discover')")
    for q in ("UPDATE query_log SET n_rows = 5", "DELETE FROM query_log", "TRUNCATE query_log"):
        with pytest.raises(psycopg2.errors.RaiseException):
            sql(q)
    assert sql("SELECT count(*) FROM query_log")[0][0] == 1


def token(user):
    """A real session for a seed user, returned as the token hash the database functions expect."""
    import scats_auth as auth
    t, _p, _e = auth.login(db_stub.REVIEW_URL, user, f"{user}123", "127.0.0.1")
    return auth.hash_token(t)


def test_a_limit_changes_only_with_an_admin_token_and_inside_its_range():
    with pytest.raises(psycopg2.Error) as e:
        run(db_stub.ACCESS_URL, "SELECT access_set_limit(%s, 'viewer', 'rate_per_minute', 10)", (token("viewer"),))
    assert "role" in str(e.value).lower() or "permission" in str(e.value).lower() or "denied" in str(e.value).lower()
    with pytest.raises(psycopg2.Error):
        run(db_stub.ACCESS_URL, "SELECT access_set_limit(%s, 'viewer', 'rate_per_minute', 10)", ("0" * 64,))
    assert run(db_stub.ACCESS_URL, "SELECT value FROM api_limit WHERE role_name='viewer' AND name='rate_per_minute'")[0][0] == 60
    old = run(db_stub.ACCESS_URL, "SELECT access_set_limit(%s, 'viewer', 'rate_per_minute', 10)", (token("admin"),))[0][0]
    assert old == 60 and run(db_stub.ACCESS_URL, "SELECT value FROM api_limit WHERE role_name='viewer' AND name='rate_per_minute'")[0][0] == 10
    row = sql("SELECT username, action, entity_id, before_state, after_state FROM audit_log WHERE action = 'limit.change'")
    assert row == [("admin", "limit.change", "viewer/rate_per_minute", {"value": 60}, {"value": 10})]
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(db_stub.ACCESS_URL, "SELECT access_set_limit(%s, 'viewer', 'rate_per_minute', 6001)", (token("admin"),))
    with pytest.raises(psycopg2.Error):
        run(db_stub.ACCESS_URL, "SELECT access_set_limit(%s, 'viewer', 'no_such_limit', 6)", (token("admin"),))
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'limit.change'")[0][0] == 1       # the refused ones left no audit row


def test_the_limit_table_itself_refuses_values_outside_the_range():
    with pytest.raises(psycopg2.errors.CheckViolation):
        sql("UPDATE api_limit SET value = 99999999 WHERE name = 'max_rows_subset'")
    with pytest.raises(psycopg2.errors.CheckViolation):
        sql("UPDATE api_limit SET value = 0 WHERE name = 'max_rows_subset'")


# ----------------------------------------------------------- products and stale marking --
def product_row(did, key="k", kind="binned_profile", version=1, manifest="m1", review_version=7):
    sql("INSERT INTO derived_product (kind, product_key, version, dataset_id, dataset_key, dataset_type, title, description, source_store_id, source_store_uri,"
        " source_manifest_sha256, source_content_sha256, source_review_id, source_review_version, uri, zarr_format, content_sha256, manifest_sha256, n_objects,"
        " total_bytes, code_version, code_sha256, config_sha256, lineage, variables, built_by) VALUES (%s,%s,%s,%s,%s,'profile','t','d',1,'s3://standardised/x',%s,'c',1,%s,"
        " 's3://derived/x',2,'c','m',1,1,'0.1.0','c','c','{}'::jsonb,'{temperature}','test')", (kind, f"{kind}__{key}", version, did, key, manifest, review_version), fetch=False)


def make_published(key="k"):
    return sql("INSERT INTO dataset (dataset_key, title, dataset_type, source, zone, storage_uri, status) VALUES (%s,'t','profile','s','raw','file:///x','published') RETURNING dataset_id",
               (key,))[0][0]


def stale_state(key="k"):
    return sql("SELECT stale, stale_reason FROM catalogue_product WHERE dataset_key = %s ORDER BY version DESC", (key,))


def add_store(did, manifest="m1", review_version=7):
    rid = sql("INSERT INTO review (dataset_id, decision, review_version) VALUES (%s,'approved',%s) RETURNING review_id", (did, review_version))[0][0]
    sql("INSERT INTO dataset_store (dataset_id, review_id, zone, store_uri, zarr_format, content_sha256, manifest_sha256, n_objects, total_bytes, review_version)"
        " VALUES (%s,%s,'standardised','s3://standardised/x',2,'c',%s,1,1,%s)", (did, rid, manifest, review_version), fetch=False)


def test_only_one_current_version_per_product():
    did = make_published()
    product_row(did, version=1)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        product_row(did, version=2)
    sql("UPDATE derived_product SET superseded_at = now() WHERE version = 1", fetch=False)
    product_row(did, version=2)


def test_a_dataset_that_has_products_cannot_be_deleted():
    did = make_published()
    product_row(did)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        sql("DELETE FROM dataset WHERE dataset_id = %s", (did,))


def test_the_trigger_marks_products_stale_when_the_dataset_leaves_published():
    did = make_published()
    add_store(did)
    product_row(did)
    assert stale_state() == [(False, None)]
    sql("UPDATE dataset SET status = 'in_review' WHERE dataset_id = %s", (did,), fetch=False)
    stale, why = stale_state()[0]
    assert stale and "left the published state" in why
    assert sql("SELECT stale_at IS NOT NULL FROM derived_product")[0][0] is True        # stored by the trigger, not only computed by the view


def test_the_trigger_marks_products_stale_when_the_store_row_changes():
    did = make_published()
    add_store(did)
    product_row(did)
    sql("UPDATE dataset_store SET manifest_sha256 = 'other' WHERE dataset_id = %s", (did,), fetch=False)
    assert sql("SELECT stale_at IS NOT NULL, stale_reason FROM derived_product")[0] == (True, "the source dataset was published again with different content or a different review")


def test_the_trigger_marks_products_stale_when_the_store_row_is_removed():
    did = make_published()
    add_store(did)
    product_row(did)
    sql("DELETE FROM dataset_store WHERE dataset_id = %s", (did,), fetch=False)
    assert sql("SELECT stale_at IS NOT NULL, stale_reason FROM derived_product")[0] == (True, "the catalogue row of the source Zarr store was removed")


def test_an_unrelated_store_update_does_not_mark_anything_stale():
    did = make_published()
    add_store(did)
    product_row(did)
    sql("UPDATE dataset_store SET staging_superseded_at = now() WHERE dataset_id = %s", (did,), fetch=False)
    assert stale_state() == [(False, None)]


def test_the_view_notices_a_changed_source_even_when_nothing_marked_it():
    """Second layer: the view compares the recorded source with the catalogue, so a missed trigger is not enough to serve old numbers."""
    did = make_published()
    add_store(did)
    product_row(did)
    sql("ALTER TABLE dataset_store DISABLE TRIGGER USER")
    try:
        sql("UPDATE dataset_store SET manifest_sha256 = 'other' WHERE dataset_id = %s", (did,), fetch=False)
    finally:
        sql("ALTER TABLE dataset_store ENABLE TRIGGER USER")
    assert sql("SELECT stale_at IS NULL FROM derived_product")[0][0] is True
    stale, why = stale_state()[0]
    assert stale and "no longer matches" in why


def test_the_view_notices_a_new_review_version():
    did = make_published()
    add_store(did)
    product_row(did)
    sql("ALTER TABLE dataset_store DISABLE TRIGGER USER")
    try:
        sql("UPDATE dataset_store SET review_version = 99 WHERE dataset_id = %s", (did,), fetch=False)
    finally:
        sql("ALTER TABLE dataset_store ENABLE TRIGGER USER")
    assert stale_state()[0][0] is True


def test_catalogue_views_show_only_published_datasets():
    for st in ("qc_done", "in_review", "approved", "rejected"):
        d = sql("INSERT INTO dataset (dataset_key, title, dataset_type, source, zone, storage_uri, status) VALUES (%s,'t','profile','s','raw','file:///x',%s) RETURNING dataset_id", (f"k_{st}", st))[0][0]
        add_store(d)                              # even with a store row, an unpublished dataset is not catalogued
    assert run(db_stub.ACCESS_URL, "SELECT count(*) FROM catalogue_dataset")[0][0] == 0
    d = make_published("pub")
    add_store(d)
    assert run(db_stub.ACCESS_URL, "SELECT dataset_key FROM catalogue_dataset") == [("pub",)]
