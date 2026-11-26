"""The data access API end to end: stand-in PostgreSQL, moto, real review flow for the published datasets, products built by the pipeline."""
import io
import os
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from moto import mock_aws

import access_helpers as A
import db_stub
from review_helpers import login, sql

pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")

ARGO, BUOY, SST, BATHY = (A.key_of(p) for p in (A.ARGO, A.BUOY, A.SST, A.BATHY))


class MP:
    def setenv(self, k, v):
        os.environ[k] = v


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    db_stub.start(fresh=True)
    db_stub.reset_data()
    saved = dict(os.environ)
    tmp = tmp_path_factory.mktemp("w")
    with mock_aws():
        A.make_buckets()
        mp = MP()
        w = SimpleNamespace(staging=tmp / "staging")
        w.review = A.reviewer_client(tmp, mp)
        for f in (A.ARGO, A.CTD, A.BUOY, A.SST, A.BATHY):
            A.publish(w, f)
        A.set_products_env(mp)
        A.register(A.tiny_series(), "hostile")                                  # a platform id that is a spreadsheet formula
        A.register(A.tiny_sst(lon=(170.0, 175.0)), "wrap")                      # not near 180 yet; replaced below
        A.register(A.tiny_profiles(), "unpub", status="in_review")
        A.register(A.tiny_profiles(), "rejected", status="rejected")
        A.register(A.tiny_profiles(), "stale_target")
        from scats_products import build_all
        results = build_all()
        assert not [r for r in results if r.status == "failed"], [r.detail for r in results]
        os.environ["DATABASE_URL"] = db_stub.ACCESS_URL
        from access_api.main import app
        w.app = app
        w.client = TestClient(app, raise_server_exceptions=False)
        w.h = SimpleNamespace(viewer=login(w.client, "viewer"), reviewer=login(w.client, "reviewer"), admin=login(w.client, "admin"))
        yield w
    os.environ.clear()
    os.environ.update(saved)
    db_stub.stop()


@pytest.fixture(autouse=True)
def tidy(world):
    """Defaults for every test: high rate limit, normal other limits, empty query log."""
    sql("ALTER TABLE query_log DISABLE TRIGGER USER")
    sql("DELETE FROM query_log")
    sql("ALTER TABLE query_log ENABLE TRIGGER USER")
    for name, value in db_stub.API_LIMIT_DEFAULTS.items():
        sql("UPDATE api_limit SET value = %s WHERE name = %s", (value, name), fetch=False)
    sql("UPDATE api_limit SET value = 6000 WHERE name = 'rate_per_minute'", fetch=False)
    sql("GRANT INSERT ON query_log TO scats_access", fetch=False)
    yield


def get(w, path, who="viewer", **params):
    return w.client.get(path, headers=getattr(w.h, who), params=params)


def set_limit(name, value):
    sql("UPDATE api_limit SET value = %s WHERE name = %s", (value, name), fetch=False)


def log_rows():
    return sql("SELECT username, action, target_type, target_key, product_version, data_version, qc_config_sha256, filter, output_format, n_rows, n_bytes FROM query_log ORDER BY log_id")


# ================================================================ access control ==
def test_every_route_has_a_policy_row_and_every_row_a_route(world):
    from access_api.policy import ROLE_MATRIX
    routes = {(m, r.path) for r in world.app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert routes == set(ROLE_MATRIX), sorted(routes ^ set(ROLE_MATRIX))


def test_a_route_without_a_policy_row_is_refused(world):
    from access_api.main import app

    @app.get("/zz_unlisted")
    def zz():
        return {"secret": 1}
    try:
        assert world.client.get("/zz_unlisted", headers=world.h.admin).status_code == 403
        assert world.client.get("/zz_unlisted").status_code == 403
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/zz_unlisted"]


def concrete(path):
    return (path.replace("{key}", "KEY").replace("{role}", "viewer").replace("{name}", "rate_per_minute"))


def test_docs_and_openapi_are_off(world):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert world.client.get(p, headers=world.h.admin).status_code in (403, 404)


def test_role_matrix_every_role_against_every_endpoint(world):
    from access_api.policy import ROLE_MATRIX
    order = {None: 0, "viewer": 1, "reviewer": 2, "admin": 3}
    wrong = []
    for (method, path), need in ROLE_MATRIX.items():
        if path in ("/auth/login", "/auth/logout"):             # logout would end the session the next rows need; tested on its own
            continue
        for who in (None, "viewer", "reviewer", "admin"):
            url = concrete(path)
            h = getattr(world.h, who) if who else {}
            kw = {"json": {"value": 100}} if method == "PUT" else {}
            r = world.client.request(method, url, headers=h, **kw)
            have = order[who]
            if need is None:
                ok = r.status_code not in (401, 403)
            elif who is None:
                ok = r.status_code == 401
            elif have < order[need]:
                ok = r.status_code == 403
            else:
                ok = r.status_code not in (401, 403)
            if not ok:
                wrong.append((method, path, who, r.status_code))
    assert not wrong, wrong


# ====================================================================== login ==
def test_login_token_logout_and_cookie(world):
    c = TestClient(world.app, raise_server_exceptions=False)
    assert c.post("/auth/login", json={"username": "viewer", "password": "wrong-password"}).status_code == 401
    r = c.post("/auth/login", json={"username": "viewer", "password": "viewer123"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    tok = r.json()["token"]
    assert c.get("/auth/me").json() == {"username": "viewer", "role": "viewer"}                   # the cookie works for reads
    assert c.get("/auth/me", headers={"Authorization": "Bearer " + tok}).status_code == 200
    assert c.put("/admin/limits/viewer/rate_per_minute", json={"value": 5}).status_code in (401, 403)
    assert c.post("/auth/logout").status_code == 403                                              # cookie + unsafe method, no X-Requested-With
    assert c.post("/auth/logout", headers={"X-Requested-With": "scats-ui"}).status_code == 200
    assert TestClient(world.app).get("/auth/me", headers={"Authorization": "Bearer " + tok}).status_code == 401      # revoked


def test_logout_needs_a_login(world):
    assert world.client.post("/auth/logout").status_code == 401
    c = TestClient(world.app)
    h = login(c, "reviewer")
    assert c.post("/auth/logout", headers=h).status_code == 200 and c.get("/auth/me", headers=h).status_code == 401


def test_bad_tokens_are_refused(world):
    for h in ({"Authorization": "Bearer nonsense"}, {"Authorization": "Bearer " + "a" * 64}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}):
        assert world.client.get("/datasets", headers=h).status_code == 401
    assert world.client.get("/datasets").status_code == 401


def test_a_disabled_user_and_an_expired_session_are_refused(world):
    c = TestClient(world.app)
    h = login(c, "viewer")
    sql("UPDATE auth_session SET expires_at = now() - interval '1 minute' WHERE user_id = 3", fetch=False)
    assert c.get("/datasets", headers=h).status_code == 401
    h = login(c, "viewer")
    sql("UPDATE app_user SET is_active = false WHERE username = 'viewer'", fetch=False)
    try:
        assert c.get("/datasets", headers=h).status_code == 401
    finally:
        sql("UPDATE app_user SET is_active = true WHERE username = 'viewer'", fetch=False)
        world.h.viewer = login(world.client, "viewer")                  # the sessions of the shared viewer were expired above


def test_a_token_from_the_review_api_works_here_too(world):
    token = login(world.review.client, "viewer")["Authorization"]     # a fresh login on the review API; earlier tests expire the old sessions
    r = TestClient(world.app).get("/auth/me", headers={"Authorization": token})
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json()["username"] == "viewer"


def test_health_is_public_and_tells_nothing_else(world):
    r = world.client.get("/health")
    assert r.status_code == 200 and r.json()["database"] == {"ok": True} and r.json()["storage"] == {"ok": True}
    assert "scats_access" not in r.text and "postgres" not in r.text.lower()
    assert world.client.get("/health/live").json() == {"status": "ok"}
    assert world.client.get("/health/audit", headers=world.h.viewer).status_code == 403
    assert world.client.get("/health/audit", headers=world.h.admin).json()["status"] == "ok"


def test_security_headers_on_every_answer(world):
    for r in (get(world, "/datasets"), world.client.get("/health"), world.client.get("/datasets"), get(world, f"/datasets/{SST}/download")):
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store" and "default-src 'none'" in r.headers["content-security-policy"]


# ================================================================== discovery ==
def keys(r):
    return {i.get("product_key") or i["dataset_key"] for i in r.json()["items"]}


def test_only_published_datasets_are_listed(world):
    r = get(world, "/datasets", page_size=200)
    assert r.status_code == 200
    ks = keys(r)
    assert {ARGO, BUOY, SST, BATHY, "hostile", "wrap", "stale_target"} <= ks and not ({"unpub", "rejected"} & ks)
    assert get(world, "/datasets/unpub").status_code == 404 and get(world, "/datasets/rejected").status_code == 404
    assert get(world, "/datasets/unpub/subset").status_code == 404 and get(world, "/datasets/unpub/download").status_code == 404
    assert {d["dataset_key"] for d in get(world, "/catalogue").json()["datasets"]} == ks


def test_search_filters(world):
    assert keys(get(world, "/datasets", dataset_type="grid")) >= {SST, "wrap"} and BUOY not in keys(get(world, "/datasets", dataset_type="grid"))
    assert keys(get(world, "/datasets", platform_type="moored_buoy")) == {BUOY, "hostile"}
    assert keys(get(world, "/datasets", variable="wind_speed")) == {BUOY, "hostile"}
    assert keys(get(world, "/datasets", q="bathymetry")) == {BATHY}
    assert get(world, "/datasets", q="no-such-thing-xyz").json()["total"] == 0
    assert keys(get(world, "/datasets", bbox="0,0,10,10")) == set()                              # nothing near 5E 5N
    assert {BUOY, SST} <= keys(get(world, "/datasets", bbox="67,14,69,16")) and "wrap" not in keys(get(world, "/datasets", bbox="67,14,69,16"))
    assert BUOY in keys(get(world, "/datasets", time_from="2025-02-01", time_to="2025-02-03"))
    assert BUOY not in keys(get(world, "/datasets", time_from="2026-01-01"))
    assert BATHY not in keys(get(world, "/datasets", time_from="2025-01-01"))                    # no time axis: not matched by a time filter


def test_search_text_is_never_run_as_sql(world):
    for q in ("' OR 1=1 --", "%", "_", "\\", "x'; DROP TABLE dataset; --", "a" * 150):
        r = get(world, "/datasets", q=q)
        assert r.status_code == 200 and r.json()["total"] in (0, 7) or q in ("%", "_")
    assert get(world, "/datasets", q="%").json()["total"] == 0                                    # a literal percent sign, not a wildcard
    assert sql("SELECT count(*) FROM dataset")[0][0] >= 9


@pytest.mark.parametrize("q", [{"dataset_type": "x"}, {"platform_type": "a b"}, {"variable": "a;b"}, {"kind": "nope"}, {"bbox": "1,2,3"}, {"time_from": "soon"},
                               {"page": "0"}, {"page_size": "9999"}, {"page": "x"}, {"zzz": "1"}, {"q": "x" * 201}])
def test_bad_search_parameters_are_refused(world, q):
    assert get(world, "/datasets", **q).status_code == 422


def test_paging_says_when_a_page_is_cut(world):
    first = get(world, "/datasets", page_size=3).json()
    assert first["total"] >= 7 and first["truncated"] is True and first["next_page"] == 2 and len(first["items"]) == 3
    last = get(world, "/datasets", page_size=3, page=3).json()
    assert last["truncated"] is False and last["next_page"] is None
    seen = []
    for p in (1, 2, 3):
        seen += [i["dataset_key"] for i in get(world, "/datasets", page_size=3, page=p).json()["items"]]
    assert len(seen) == len(set(seen)) == first["total"]


def test_products_are_listed_with_their_kind_and_hide_stale_ones(world):
    r = get(world, "/products", page_size=200)
    ks = keys(r)
    assert {f"binned_profile__{ARGO}", f"series_hourly__{BUOY}", f"series_daily__{BUOY}", f"sst_monthly_mean__{SST}"} <= ks
    assert not any(k.endswith(BATHY) for k in ks)                                                # no bathymetry product
    assert keys(get(world, "/products", kind="series_daily")) == {f"series_daily__{BUOY}", "series_daily__hostile"}
    sql("UPDATE derived_product SET stale_at = now(), stale_reason = 'test' WHERE product_key = 'binned_profile__stale_target'", fetch=False)
    try:
        assert "binned_profile__stale_target" not in keys(get(world, "/products", page_size=200))
        assert "binned_profile__stale_target" in keys(get(world, "/products", page_size=200, include_stale="true"))
    finally:
        sql("UPDATE derived_product SET stale_at = NULL, stale_reason = NULL WHERE product_key = 'binned_profile__stale_target'", fetch=False)


# ================================================================== metadata ==
def test_metadata_carries_what_a_user_needs_to_cite(world):
    d = get(world, f"/datasets/{BUOY}").json()
    assert len(d["data_version"]) == 64 and d["product_version"] is None and d["qc_config_version"] == "0.2.0" and len(d["qc_config_sha256"]) == 64
    assert "store_uri" not in d and d["stale"] is False
    p = get(world, f"/products/series_daily__{BUOY}").json()
    assert p["product_version"] == 1 and p["flags_used"] == [1, 2] and p["lineage"]["source"]["dataset_key"] == BUOY and p["versions"][0]["version"] == 1
    assert p["lineage"]["code_sha256"] and p["lineage"]["config_sha256"] and len(p["qc_config_sha256"]) == 64 and "uri" not in p


@pytest.mark.parametrize("key", ["../etc/passwd", "a b", "x" * 200, "%00", "nope", ";drop", "a/b"])
def test_unknown_or_hostile_keys_are_404(world, key):
    for base in ("/datasets/", "/products/"):
        r = world.client.get(base + key, headers=world.h.viewer)
        assert r.status_code == 404, (key, r.status_code)


# ==================================================================== subset ==
def source(key, bucket="standardised", prefix_of=None):
    from scats_store import S3Store, read_store
    row = sql("SELECT store_uri, manifest_sha256 FROM dataset_store s JOIN dataset d USING (dataset_id) WHERE d.dataset_key = %s", (key,))[0]
    bkt, pre = row[0][5:].split("/", 1)
    return read_store(S3Store.from_env(), bkt, pre, expected_manifest_sha256=row[1])


def test_subset_answers_carry_the_citation_fields(world):
    b = get(world, f"/datasets/{BUOY}/subset", page_size=5).json()
    assert b["data_version"] == get(world, f"/datasets/{BUOY}").json()["data_version"] and b["qc_config_version"] == "0.2.0" and len(b["qc_config_sha256"]) == 64
    assert b["filter"] == {"quality": "good"} and b["product_version"] is None and "stale" in b and b["units"]["wind_speed"] == "m s-1"
    p = get(world, f"/products/series_hourly__{BUOY}/subset", page_size=2).json()
    assert p["product_version"] == 1 and p["flags_used"] == [1, 2] and p["filter"] == {"quality": "good"}


def test_sst_subset_matches_the_stored_data(world):
    ds = source(SST)
    t0, t1 = np.datetime64("2025-01-10"), np.datetime64("2025-01-12T23:59:59")
    la = ds["latitude"].values
    lo = ds["longitude"].values
    ti = np.nonzero((ds["time"].values >= t0) & (ds["time"].values <= t1))[0]
    yi = np.nonzero((la >= 10) & (la <= 12))[0]
    xi = np.nonzero((lo >= 65) & (lo <= 67))[0]
    want = ds["sea_surface_temperature"].values[np.ix_(ti, yi, xi)]
    r = get(world, f"/datasets/{SST}/subset", time_from="2025-01-10", time_to="2025-01-12", bbox="65,10,67,12", variable="sea_surface_temperature", quality="all", page_size=200).json()
    assert r["n_rows_selected"] == want.size == 3 * len(yi) * len(xi)
    got = pd.DataFrame(r["rows"])
    assert got["sea_surface_temperature"].tolist() == [None if np.isnan(x) else x for x in want.ravel()[:200]]
    assert got.columns.tolist() == ["time", "latitude", "longitude", "sea_surface_temperature", "sea_surface_temperature_qc"]
    assert got["time"].iloc[0] == "2025-01-10T00:00:00Z" and got["latitude"].iloc[0] == la[yi[0]] and got["longitude"].iloc[0] == lo[xi[0]]


def test_quality_good_hides_values_with_other_flags(world):
    # the "hostile" dataset is tiny_series: record 4 has wind_speed 100 with flag 3, record 5 has a bad time flag (4)
    base = "/datasets/hostile/subset"
    allr = get(world, base, variable="wind_speed", quality="all", page_size=50).json()
    goodr = get(world, base, variable="wind_speed", quality="good", page_size=50).json()
    assert allr["n_rows_selected"] == 6
    assert sorted(x["wind_speed"] for x in allr["rows"]) == [2.0, 4.0, 6.0, 8.0, 50.0, 100.0]
    assert goodr["n_rows_selected"] == 5                                              # the record with a bad time flag is not served as good
    by = {x["wind_speed"]: x for x in goodr["rows"]}
    assert sorted(v for v in by if v is not None) == [2.0, 4.0, 6.0, 8.0] and None in by      # the flag-3 value 100 is hidden, not dropped
    assert 100.0 not in by and 50.0 not in by
    hidden = [x for x in goodr["rows"] if x["wind_speed"] is None]
    assert len(hidden) == 1 and hidden[0]["wind_speed_qc"] == 3


def test_a_hand_checked_profile_subset(world):
    r = get(world, "/datasets/stale_target/subset", quality="all", depth_min="2", depth_max="6", platform="A,B", page_size=50).json()
    # profiles A and B, levels with depth 2..6: A has depths 2 and 6 (12, 20), B has 3 and 6 (13 flag 4, 21)
    assert r["n_rows_selected"] == 4 and [x["temperature"] for x in r["rows"]] == [12.0, 20.0, 13.0, 21.0] and [x["temperature_qc"] for x in r["rows"]] == [2, 1, 4, 2]
    g = get(world, "/datasets/stale_target/subset", depth_min="2", depth_max="6", platform="A,B", page_size=50).json()
    assert g["n_rows_selected"] == 4 and [x["temperature"] for x in g["rows"]] == [12.0, 20.0, None, 21.0]       # B's flag-4 value is hidden
    # profile C has a bad position flag: with good quality it cannot be located, so it is not selected; with all it is
    assert get(world, "/datasets/stale_target/subset", platform="C").json()["n_rows_selected"] == 0
    assert get(world, "/datasets/stale_target/subset", platform="C", quality="all").json()["n_rows_selected"] == 5


def test_paging_through_a_subset_gives_every_row_once(world):
    pages = []
    p = 1
    while True:
        r = get(world, f"/datasets/{BUOY}/subset", variable="wind_speed", page_size=200, page=p, time_from="2025-01-01", time_to="2025-01-05").json()
        pages += r["rows"]
        assert r["truncated"] == (r["next_page"] is not None)
        if not r["truncated"]:
            break
        p = r["next_page"]
    assert r["n_rows_selected"] == len(pages) == 5 * 24 and len({x["source_record"] for x in pages}) == 120


def test_an_empty_selection_is_a_valid_answer(world):
    r = get(world, f"/datasets/{SST}/subset", bbox="10,-50,20,-40")
    assert r.status_code == 200 and r.json()["n_rows_selected"] == 0 and r.json()["rows"] == [] and r.json()["truncated"] is False
    r = get(world, f"/datasets/{BUOY}/subset", time_from="2030-01-01")
    assert r.status_code == 200 and r.json()["rows"] == []
    assert get(world, f"/datasets/{BUOY}/download", time_from="2030-01-01").text.splitlines()[0].startswith("platform_id,source_record,time")


def test_a_box_across_the_antimeridian_through_the_api(world):
    ds = A.tiny_sst()
    ds = ds.assign_coords(longitude=("longitude", [179.5, -179.5], {"units": "degrees_east"}))
    ds.attrs["geospatial_lon_min"], ds.attrs["geospatial_lon_max"] = "-179.5", "179.5"
    A.register(ds, "dateline")
    r = get(world, "/datasets/dateline/subset", bbox="179,10,-179,10.25", quality="all", time_from="2025-01-30", time_to="2025-01-30").json()
    assert r["n_rows_selected"] == 4 and sorted({x["longitude"] for x in r["rows"]}) == [-179.5, 179.5]
    r = get(world, "/datasets/dateline/subset", bbox="-180,10,0,10.25", quality="all", time_from="2025-01-30", time_to="2025-01-30").json()
    assert {x["longitude"] for x in r["rows"]} == {-179.5}
    r = get(world, "/datasets/dateline/subset", bbox="0,10,180,10.25", quality="all", time_from="2025-01-30", time_to="2025-01-30").json()
    assert {x["longitude"] for x in r["rows"]} == {179.5}


@pytest.mark.parametrize("params", [
    {"bbox": "1,2,3"}, {"bbox": "0,-91,10,10"}, {"bbox": "0,0,10,91"}, {"bbox": "0,20,10,10"}, {"bbox": "nan,0,1,1"}, {"bbox": "0,0,1e999,1"}, {"bbox": "1_0,0,5,5"},
    {"time_from": "2025-02-30"}, {"time_from": "'; DROP TABLE dataset;--"}, {"time_from": "2025-02-02", "time_to": "2025-02-01"}, {"time_to": "99999-01-01"},
    {"variable": "../../../etc/passwd"}, {"variable": "sea_surface_temperature;x"}, {"variable": "nope"}, {"depth_min": "1"}, {"platform": "A"}, {"quality": "everything"},
    {"page": "-1"}, {"page": "1.5"}, {"page_size": "0"}, {"page_size": "100000"}, {"page_size": "9" * 40}, {"unknown": "x"}, {"format": "csv"} if False else {"version": "1"},
])
def test_hostile_or_malformed_filters_give_422_never_500(world, params):
    r = get(world, f"/datasets/{SST}/subset", **params)
    assert r.status_code == 422, (params, r.status_code, r.text[:100])
    assert sql("SELECT count(*) FROM dataset")[0][0] >= 9                                       # nothing was run as SQL
    assert len(r.text) < 600


def test_repeated_and_control_character_parameters_are_refused(world):
    assert world.client.get(f"/datasets/{SST}/subset?variable=sea_surface_temperature&variable=sea_surface_temperature", headers=world.h.viewer).status_code == 422
    assert world.client.get(f"/datasets/{SST}/subset?bbox=1,2,3,4%00", headers=world.h.viewer).status_code == 422
    assert world.client.get(f"/datasets/{SST}/subset?time_from=2025-01-01%0d%0aX-Evil:1", headers=world.h.viewer).status_code == 422


def test_product_versions_can_be_asked_for_and_datasets_have_none(world):
    assert get(world, f"/products/series_daily__{BUOY}/subset", version="1", page_size=1).status_code == 200
    assert get(world, f"/products/series_daily__{BUOY}/subset", version="2").status_code == 404
    assert get(world, f"/products/series_daily__{BUOY}/subset", version="x").status_code == 422
    assert get(world, f"/datasets/{BUOY}/subset", version="1").status_code == 422


def test_a_product_subset_matches_the_hand_values(world):
    r = get(world, "/products/binned_profile__stale_target/subset", page_size=50).json()
    rows = r["rows"]
    t = {(x["platform_id"], x["bin"]): x["temperature"] for x in rows}
    assert t[("A", 0)] == 11.0 and t[("A", 1)] == 20.0 and t[("B", 0)] == 11.0 and t[("B", 1)] == 21.0 and t[("B", 2)] == 23.0 and t[("C", 0)] is None
    h = get(world, "/products/series_hourly__hostile/subset", page_size=10, variable="wind_speed_mean,wind_speed_count").json()["rows"]
    assert [x["wind_speed_mean"] for x in h] == [3.0, 6.0, None, 8.0] and [x["wind_speed_count"] for x in h] == [2, 1, 0, 1]


# ================================================================== download ==
def test_csv_download_equals_the_stored_data(world):
    ds = source(SST)
    r = get(world, f"/datasets/{SST}/download", format="csv", quality="all", variable="sea_surface_temperature")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="{SST}.csv"'
    df = pd.read_csv(io.StringIO(r.text), float_precision="round_trip")
    want = ds["sea_surface_temperature"].values
    assert len(df) == want.size == int(r.headers["x-scats-rows"])
    got = df["sea_surface_temperature"].to_numpy().reshape(want.shape)
    np.testing.assert_array_equal(got, want)
    assert df["sea_surface_temperature_qc"].to_numpy().reshape(want.shape).tolist() == ds["sea_surface_temperature_qc"].values.tolist()
    assert r.headers["x-scats-data-version"] == get(world, f"/datasets/{SST}").json()["data_version"] and r.headers["x-scats-flags-used"] == "all"
    assert len(r.headers["x-scats-qc-config-sha256"]) == 64 and r.headers["x-scats-stale"] == "false"


def test_netcdf_download_equals_the_stored_data(world, tmp_path):
    ds = source(ARGO)
    r = get(world, f"/datasets/{ARGO}/download", format="netcdf", quality="all", variable="temperature,salinity")
    assert r.status_code == 200 and r.headers["content-disposition"] == f'attachment; filename="{ARGO}.nc"'
    p = tmp_path / "a.nc"
    p.write_bytes(r.content)
    nc = xr.open_dataset(p)
    depth = ds["depth"].values
    keep = np.isfinite(depth)
    np.testing.assert_array_equal(nc["temperature"].values, ds["temperature"].values[keep])
    np.testing.assert_array_equal(nc["depth"].values, depth[keep])
    np.testing.assert_array_equal(nc["temperature_qc"].values, ds["temperature_qc"].values[keep])
    assert nc.attrs["data_version"] == r.headers["x-scats-data-version"] and nc.attrs["qc_config_sha256"] == r.headers["x-scats-qc-config-sha256"]
    assert nc["temperature"].attrs["units"] == "degC" and nc.attrs["flags_used"].startswith("all")


def test_product_download_and_its_version_headers(world):
    r = get(world, f"/products/series_daily__{BUOY}/download", format="csv")
    assert r.status_code == 200 and r.headers["x-scats-product-version"] == "1" and r.headers["x-scats-flags-used"] == "1,2"
    assert r.headers["content-disposition"] == f'attachment; filename="series_daily__{BUOY}_v1.csv"'
    df = pd.read_csv(io.StringIO(r.text))
    assert len(df) == 90 and df["wind_speed_count"].max() == 24


def test_csv_cells_that_would_run_as_formulas_are_neutralised(world):
    r = get(world, "/datasets/hostile/download", format="csv", quality="all")
    assert r.status_code == 200
    first = r.text.splitlines()[1]
    assert first.startswith("\"'=cmd|' /C calc'!A0\",") or first.startswith("'=cmd|"), first
    assert not any(line.startswith("=") for line in r.text.splitlines())
    j = get(world, "/datasets/hostile/subset", quality="all", page_size=1).json()
    assert j["rows"][0]["platform_id"] == "=cmd|' /C calc'!A0"                                  # JSON is data, not a spreadsheet: left as it is


def test_numbers_that_start_with_a_minus_are_not_damaged_in_csv(world):
    ds = A.tiny_sst()
    ds = ds.assign_coords(longitude=("longitude", [-179.5, -170.25], {"units": "degrees_east"}))
    ds.attrs["geospatial_lon_min"], ds.attrs["geospatial_lon_max"] = "-179.5", "-170.25"
    A.register(ds, "negative")
    r = get(world, "/datasets/negative/download", format="csv", quality="all")
    assert ",-179.5," in r.text and "'-" not in r.text
    df = pd.read_csv(io.StringIO(r.text))
    assert df["longitude"].dtype.kind == "f" and sorted(df["longitude"].unique().tolist()) == [-179.5, -170.25]


def test_downloads_have_server_made_names_and_a_known_format(world):
    assert get(world, f"/datasets/{SST}/download", format="exe").status_code == 422
    assert get(world, f"/datasets/{SST}/download", format="csv\r\nX: y").status_code == 422


# =================================================================== limits ==
def test_every_limit_is_enforced_once(world):
    # page_size_max
    set_limit("page_size_max", 10)
    r = get(world, f"/datasets/{BUOY}/subset", page_size=11)
    assert r.status_code == 422 and "between 1 and 10" in r.text
    assert get(world, f"/datasets/{BUOY}/subset", page_size=10, time_to="2025-01-02").status_code == 200
    # max_rows_subset: refused, not cut
    set_limit("max_rows_subset", 100)
    r = get(world, f"/datasets/{BUOY}/subset")
    assert r.status_code == 413 and "2160 rows" in r.text and "max_rows_subset = 100" in r.text
    # max_rows_download
    set_limit("max_rows_download", 100)
    r = get(world, f"/datasets/{BUOY}/download")
    assert r.status_code == 413 and "max_rows_download = 100" in r.text and not r.content.startswith(b"platform_id")
    # max_bytes_download
    set_limit("max_rows_download", 500000)
    set_limit("max_bytes_download", 1000)
    r = get(world, f"/datasets/{BUOY}/download")
    assert r.status_code == 413 and "max_bytes_download = 1000" in r.text
    # max_time_span_days: a long request is refused, a short one is fine, and "no time filter" counts as the whole collection
    set_limit("max_bytes_download", 104857600)
    set_limit("max_rows_subset", 5000)
    set_limit("max_time_span_days", 30)
    assert get(world, f"/datasets/{BUOY}/subset", time_from="2025-01-01", time_to="2025-03-01").status_code == 413
    r = get(world, f"/datasets/{BUOY}/subset")
    assert r.status_code == 413 and "time_from and time_to" in r.text
    assert get(world, f"/datasets/{BUOY}/subset", time_from="2025-01-01", time_to="2025-01-20", page_size=5).status_code == 200
    # max_bbox_area_deg2
    set_limit("max_time_span_days", 366)
    set_limit("max_bbox_area_deg2", 4)
    r = get(world, f"/datasets/{SST}/subset", bbox="60,8,70,18")
    assert r.status_code == 413 and "max_bbox_area_deg2 = 4" in r.text
    assert get(world, f"/datasets/{SST}/subset", bbox="65,10,66,11", time_to="2025-01-02").status_code == 200


def test_the_time_budget_stops_a_slow_request(world, monkeypatch):
    from scats_store import S3Store
    set_limit("request_timeout_s", 1)
    real = S3Store.get_bytes

    def slow(self, bucket, key):
        time.sleep(0.6)
        return real(self, bucket, key)
    monkeypatch.setattr(S3Store, "get_bytes", slow)
    r = get(world, f"/datasets/{SST}/subset", variable="sea_surface_temperature", bbox="65,10,66,11", time_to="2025-01-02", page_size=5)
    assert r.status_code == 504 and "request_timeout_s = 1" in r.text


def test_the_rate_limit_counts_answered_requests_per_user(world):
    set_limit("rate_per_minute", 3)
    ok = [get(world, "/datasets", page_size=1).status_code for _ in range(5)]
    assert ok == [200, 200, 200, 429, 429]
    r = get(world, "/datasets", page_size=1)
    assert r.headers["retry-after"] == "60" and "rate_per_minute = 3" in r.text
    assert get(world, "/datasets", who="reviewer", page_size=1).status_code == 200                # another user is not affected


def test_limits_can_be_read_and_an_admin_can_change_them(world):
    r = get(world, "/limits").json()
    assert r["role"] == "viewer" and {x["name"] for x in r["limits"]} == set(db_stub.API_LIMIT_DEFAULTS) and all(x["hard_max"] >= x["value"] for x in r["limits"])
    body = {"json": {"value": 77}}
    assert world.client.put("/admin/limits/viewer/max_rows_subset", headers=world.h.viewer, **body).status_code == 403
    assert world.client.put("/admin/limits/viewer/max_rows_subset", headers=world.h.reviewer, **body).status_code == 403
    r = world.client.put("/admin/limits/viewer/max_rows_subset", headers=world.h.admin, **body)
    assert r.status_code == 200 and r.json() == {"role": "viewer", "name": "max_rows_subset", "old": 5000, "new": 77}
    assert get(world, f"/datasets/{BUOY}/subset", time_to="2025-01-10").status_code == 413
    assert sql("SELECT username, entity_id FROM audit_log WHERE action = 'limit.change' AND entity_id = 'viewer/max_rows_subset'") == [("admin", "viewer/max_rows_subset")]
    for url, code in (("/admin/limits/viewer/nope", 404), ("/admin/limits/king/max_rows_subset", 404), ("/admin/limits/viewer/max_rows_subset", 422)):
        r = world.client.put(url, headers=world.h.admin, json={"value": 10**9 if code == 422 else 5})
        assert r.status_code == code, (url, r.status_code)
    assert world.client.put("/admin/limits/viewer/max_rows_subset", headers=world.h.admin, json={"value": 0}).status_code == 422


# ================================================================ query log ==
def test_every_answered_request_is_logged_and_refused_ones_are_not(world):
    get(world, "/datasets", q="buoy")
    get(world, f"/datasets/{BUOY}")
    get(world, f"/datasets/{BUOY}/zarr")
    get(world, f"/datasets/{BUOY}/subset", time_to="2025-01-01", variable="wind_speed")
    get(world, f"/products/series_daily__{BUOY}/download", format="csv")
    get(world, f"/datasets/{BUOY}/subset", bbox="bad")                 # refused: 422
    get(world, "/datasets/unpub")                                      # refused: 404
    rows = log_rows()
    assert [r[1] for r in rows] == ["discover", "metadata", "zarr_listing", "subset", "download"]
    assert all(r[0] == "viewer" for r in rows)
    d = dict(zip(("user", "action", "type", "key", "pv", "dv", "qc", "filter", "fmt", "n_rows", "n_bytes"), rows[3]))
    assert d["key"] == BUOY and d["filter"]["variable"] == ["wind_speed"] and d["n_rows"] == 24 and d["dv"] == get(world, f"/datasets/{BUOY}").json()["data_version"]
    d = dict(zip(("user", "action", "type", "key", "pv", "dv", "qc", "filter", "fmt", "n_rows", "n_bytes"), rows[4]))
    assert d["type"] == "product" and d["pv"] == 1 and d["fmt"] == "csv" and d["n_rows"] == 90 and d["n_bytes"] > 1000 and len(d["qc"]) == 64


def test_if_the_log_row_cannot_be_written_nothing_is_returned(world):
    sql("REVOKE INSERT ON query_log FROM scats_access", fetch=False)
    for path, params in ((f"/datasets/{BUOY}/subset", {"time_to": "2025-01-01"}), (f"/datasets/{BUOY}/download", {"format": "csv", "time_to": "2025-01-01"}),
                         (f"/datasets/{BUOY}/zarr", {}), ("/datasets", {}), (f"/datasets/{BUOY}", {})):
        r = get(world, path, **params)
        assert r.status_code == 503 and "nothing was returned" in r.text or r.status_code in (403, 503), (path, r.status_code)
        assert r.status_code != 200 and "wind_speed" not in r.text and not r.headers.get("content-disposition"), path
    assert sql("SELECT count(*) FROM query_log")[0][0] == 0


def test_a_failure_in_the_log_code_also_refuses_the_answer(world, monkeypatch):
    from access_api import service

    def boom(*a, **k):
        raise RuntimeError("log failed")
    monkeypatch.setattr(service, "write_log", boom)
    r = get(world, f"/datasets/{BUOY}/download", format="csv", time_to="2025-01-01")
    assert r.status_code == 503 and "wind_speed" not in r.text and "platform_id" not in r.text


# ==================================================================== stale ==
def test_a_stale_product_is_flagged_and_not_served_unless_asked(world):
    sql("UPDATE dataset_store SET review_version = review_version + 1 WHERE dataset_id = (SELECT dataset_id FROM dataset WHERE dataset_key = 'stale_target')", fetch=False)
    try:
        k = "binned_profile__stale_target"
        m = get(world, f"/products/{k}").json()
        assert m["stale"] is True and m["stale_reason"]
        for what in ("subset", "download"):
            r = get(world, f"/products/{k}/{what}")
            assert r.status_code == 409 and "stale" in r.text and "allow_stale=true" in r.text
        r = get(world, f"/products/{k}/subset", allow_stale="true", page_size=3)
        assert r.status_code == 200 and r.json()["stale"] is True and r.json()["stale_reason"]
        assert get(world, f"/products/{k}/download", allow_stale="true").headers["x-scats-stale"] == "true"
        assert get(world, f"/products/{k}/subset", allow_stale="maybe").status_code == 422
        assert get(world, f"/products/{k}/zarr").status_code == 200                              # a listing is metadata, not numbers
    finally:
        sql("UPDATE dataset_store SET review_version = review_version - 1 WHERE dataset_id = (SELECT dataset_id FROM dataset WHERE dataset_key = 'stale_target')", fetch=False)
    # the mark stored by the trigger stays even though the source went back: only a rebuild clears it
    assert get(world, "/products/binned_profile__stale_target").json()["stale"] is True
    sql("UPDATE derived_product SET stale_at = NULL, stale_reason = NULL WHERE product_key = 'binned_profile__stale_target'", fetch=False)
    assert get(world, "/products/binned_profile__stale_target").json()["stale"] is False


# ============================================================== lazy reading ==
def test_a_small_request_reads_few_objects(world, monkeypatch):
    from scats_store import S3Store
    fetched = []
    real = S3Store.get_bytes
    monkeypatch.setattr(S3Store, "get_bytes", lambda self, b, k: (fetched.append((b, k)), real(self, b, k))[1])
    total = sql("SELECT n_objects FROM derived_product WHERE product_key = %s", (f"series_hourly__{BUOY}",))[0][0]
    r = get(world, f"/products/series_hourly__{BUOY}/subset", variable="wind_speed_mean", time_from="2025-01-01", time_to="2025-01-02", page_size=5)
    assert r.status_code == 200 and r.json()["n_rows_selected"] == 48
    names = [k for b, k in fetched if b == "derived"]
    assert len(names) < total / 3, (len(names), total)
    assert any("/wind_speed_mean/" in k for k in names)
    assert not any("/air_pressure_mean/" in k or "/wave_height_max/" in k or "/wind_speed_max/" in k for k in names)    # arrays nobody asked for are never read
    fetched.clear()
    r = get(world, f"/datasets/{SST}/subset", variable="sea_surface_temperature", quality="all", bbox="65,10,66,11", time_to="2025-01-02", page_size=5)
    assert r.status_code == 200
    names = [k for b, k in fetched if b == "standardised"]
    assert not any("/time_qc/" in k for k in names) and any("/sea_surface_temperature/0" in k for k in names)


def test_a_changed_object_in_the_store_is_caught_and_nothing_is_returned(world):
    from scats_store import S3Store
    s3 = S3Store.from_env()
    uri = sql("SELECT store_uri FROM dataset_store s JOIN dataset d USING (dataset_id) WHERE d.dataset_key = %s", (BATHY,))[0][0]
    bucket, prefix = uri[5:].split("/", 1)
    key = f"{prefix}/bathymetry/0.0"
    good = s3.get_bytes(bucket, key)
    s3.put_bytes(bucket, key, good[:-1] + bytes([good[-1] ^ 1]))
    try:
        r = get(world, f"/datasets/{BATHY}/subset", page_size=3, bbox="65,10,66,11")
        assert r.status_code == 502 and "manifest" in r.text and "rows" not in r.text
        assert get(world, f"/datasets/{BATHY}/download", bbox="65,10,66,11").status_code == 502
    finally:
        s3.put_bytes(bucket, key, good)
    assert get(world, f"/datasets/{BATHY}/subset", page_size=3, bbox="65,10,66,11").status_code == 200


def test_a_store_that_does_not_match_the_catalogue_is_refused(world):
    sql("UPDATE dataset_store SET manifest_sha256 = repeat('0', 64) WHERE dataset_id = (SELECT dataset_id FROM dataset WHERE dataset_key = %s)", (BATHY,), fetch=False)
    try:
        assert get(world, f"/datasets/{BATHY}/subset", bbox="65,10,66,11").status_code in (500, 502)
        assert get(world, f"/datasets/{BATHY}/zarr").status_code == 502
    finally:
        from scats_store import S3Store
        real = S3Store.from_env().get_bytes("standardised", sql("SELECT store_uri FROM dataset_store s JOIN dataset d USING (dataset_id) WHERE d.dataset_key = %s", (BATHY,))[0][0][len("s3://standardised/"):] + "/scats_manifest.json")
        import json
        sql("UPDATE dataset_store SET manifest_sha256 = %s WHERE dataset_id = (SELECT dataset_id FROM dataset WHERE dataset_key = %s)", (json.loads(real)["manifest_sha256"], BATHY), fetch=False)
    assert get(world, f"/datasets/{BATHY}/subset", page_size=1, bbox="65,10,66,11").status_code == 200


def test_zarr_listing(world):
    r = get(world, f"/products/sst_monthly_mean__{SST}/zarr").json()
    assert r["n_objects"] == len(r["objects"]) and all(set(o) == {"path", "size", "sha256"} for o in r["objects"]) and r["zarr_format"] == 2
    assert r["manifest_sha256"] == get(world, f"/products/sst_monthly_mean__{SST}").json()["manifest_sha256"] and any(o["path"] == ".zmetadata" for o in r["objects"])
    assert "store_uri" in get(world, f"/datasets/{SST}/zarr").json() or get(world, f"/datasets/{SST}/zarr").json()["uri"].startswith("s3://standardised/")


# ================================================================== Phase 5 additions ==
def test_health_has_only_booleans_and_names_no_account(world):
    j = world.client.get("/health").json()
    assert set(j) == {"status", "service", "version", "database", "storage", "default_passwords"}
    assert j["default_passwords"] in (True, False, None)
    for name in ("admin", "reviewer", "viewer"):
        assert name not in world.client.get("/health").text.replace("scats-data-access-api", "")


def test_the_extra_security_headers_and_the_public_dashboard_routes(world):
    for r in (world.client.get("/health"), get(world, "/datasets")):
        assert r.headers["x-frame-options"] == "DENY" and r.headers["cross-origin-resource-policy"] == "same-origin"
        assert "camera=()" in r.headers["permissions-policy"]
    page = world.client.get("/app")
    assert page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"] and page.headers["cache-control"] == "no-store"
    assert world.client.get("/app/app.js").status_code == 200
    for bad in ("/app/package.json", "/app/..%2f..%2fmain.py", "/app/nothing.js"):
        assert world.client.get(bad).status_code in (403, 404), bad
    root = world.client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/app"
