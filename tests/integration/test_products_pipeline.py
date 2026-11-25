"""Products pipeline on the stand-in database and moto: published-only input, lineage, determinism, versions, stale, safe write."""
import json

import pytest
from moto import mock_aws

import access_helpers as A
import db_stub
from review_helpers import sql

pytestmark = pytest.mark.skipif(not db_stub.available(), reason="no local PostgreSQL 16 binaries")


@pytest.fixture(scope="module", autouse=True)
def server():
    db_stub.start(fresh=True)
    yield
    db_stub.stop()


@pytest.fixture(autouse=True)
def world(monkeypatch):
    db_stub.reset_data()
    A.set_products_env(monkeypatch)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        A.make_buckets()
        yield


def build(key, kind, **kw):
    from scats_products import build_product
    return build_product(key, kind, **kw)


def derived_objects(prefix=""):
    from scats_store import S3Store
    return S3Store.from_env().list("derived", prefix)


def rows(key=None):
    q = "SELECT product_key, version, superseded_at IS NOT NULL, content_sha256, manifest_sha256 FROM derived_product"
    return sql(q + (" WHERE dataset_key = %s" if key else "") + " ORDER BY product_key, version", (key,) if key else None)


# ------------------------------------------------------------------ input --
@pytest.mark.parametrize("status", ["ingested", "qc_running", "qc_done", "in_review", "approved", "rejected"])
@pytest.mark.parametrize("store", [False, True])
def test_only_published_data_is_ever_read(status, store):
    from scats_products import ProductError
    A.register(A.tiny_profiles(), "p1", status=status, store=store)
    with pytest.raises(ProductError) as e:
        build("p1", "binned_profile")
    assert f"'{status}'" in str(e.value) and "only from published data" in str(e.value)
    assert rows() == [] and derived_objects() == {}


def test_an_unknown_dataset_or_key_is_refused():
    from scats_products import ProductError
    for key in ("nope", "../x", "a b", ""):
        with pytest.raises(ProductError):
            build(key, "binned_profile")


def test_a_product_that_does_not_fit_the_dataset_is_refused():
    from scats_products import ProductError
    A.register(A.tiny_profiles(), "p1")
    with pytest.raises(ProductError) as e:
        build("p1", "series_hourly")
    assert "not defined for a profile dataset" in str(e.value)


def test_the_build_reads_only_the_standardised_store(monkeypatch, tmp_path):
    """Raw and staging do not exist (the dataset's storage_uri points nowhere); only 'standardised' is read from S3."""
    from scats_store import S3Store
    A.register(A.tiny_profiles(), "p1")
    seen = set()
    real = S3Store.get_bytes
    monkeypatch.setattr(S3Store, "get_bytes", lambda self, bucket, key: (seen.add(bucket), real(self, bucket, key))[1])
    real_list = S3Store.list
    monkeypatch.setattr(S3Store, "list", lambda self, bucket, prefix: (seen.add(bucket), real_list(self, bucket, prefix))[1])
    res = build("p1", "binned_profile")
    assert res.status == "built" and res.version == 1
    assert seen - {"derived"} == {"standardised"}               # 'derived' is read only to verify what was just written


def test_build_all_covers_every_published_dataset_and_skips_what_has_no_product():
    from scats_products import build_all
    A.register(A.tiny_profiles(), "p1")
    A.register(A.tiny_series(), "s1")
    A.register(A.tiny_sst(), "g1")
    A.register(A.tiny_profiles(), "unpublished", status="in_review")
    ds = A.tiny_sst()
    ds.attrs.update(dataset_type="bathymetry")
    ds = ds.rename({"sea_surface_temperature": "bathymetry", "sea_surface_temperature_qc": "bathymetry_qc"}).isel(time=0, drop=True).drop_vars("time_qc")
    A.register(ds, "b1")
    out = {r.product_key: r.status for r in build_all()}
    assert out == {"binned_profile__p1": "built", "series_hourly__s1": "built", "series_daily__s1": "built", "sst_monthly_mean__g1": "built", "-__b1": "skipped"}
    assert [r[0] for r in rows()] == ["binned_profile__p1", "series_daily__s1", "series_hourly__s1", "sst_monthly_mean__g1"]


def test_one_failing_build_does_not_stop_the_others(monkeypatch):
    from scats_products import build_all, pipeline
    A.register(A.tiny_profiles(), "p1")
    A.register(A.tiny_sst(), "g1")
    real = pipeline.read_store
    monkeypatch.setattr(pipeline, "read_store", lambda s3, b, p, **kw: (_ for _ in ()).throw(RuntimeError("x")) if "p1" in p else real(s3, b, p, **kw))
    try:
        out = {r.product_key: r.status for r in build_all()}
    except RuntimeError:
        pytest.fail("an unexpected error escaped build_all")
    assert out["sst_monthly_mean__g1"] == "built" and out["binned_profile__p1"] in ("failed",)


# ---------------------------------------------------------------- lineage --
def test_the_lineage_names_exactly_what_the_product_came_from():
    from scats_products import code_sha256, load_config
    src = A.register(A.tiny_profiles(), "p1", review_version=7, qc_version="0.2.0")
    res = build("p1", "binned_profile")
    lin, code, cfg, ver, uri = sql("SELECT lineage, code_sha256, config_sha256, version, uri FROM derived_product")[0]
    assert uri == "s3://derived/binned_profile/p1/v1.zarr" == res.uri and ver == 1
    assert code == code_sha256() and cfg == load_config().sha256
    assert lin["source"]["store_manifest_sha256"] == src["manifest_sha256"] and lin["source"]["store_content_sha256"] == src["content_sha256"]
    assert lin["source"]["review_version"] == 7 and lin["source"]["review_id"] == src["review_id"] and lin["source"]["qc_config_version"] == "0.2.0"
    from scats_common.qc_config_ref import qc_config_sha256_for
    assert lin["source"]["qc_config_sha256"] == qc_config_sha256_for("0.2.0") and len(lin["source"]["qc_config_sha256"]) == 64
    assert lin["flags_used"] == [1, 2] and lin["parameters"]["depth_bin_edges_m"][0] == 0 and lin["code_sha256"] == code
    # the same record is inside the Zarr store itself
    from scats_store import S3Store, read_store
    ds = read_store(S3Store.from_env(), "derived", "binned_profile/p1/v1.zarr", expected_manifest_sha256=res.manifest_sha256)
    assert json.loads(ds.attrs["scats_lineage"]) == lin and ds.attrs["flags_used"] == [1, 2] and ds.attrs["interpolation"] == "none"
    assert ds["temperature"].attrs["units"] == "degC"


def test_the_qc_config_checksum_is_not_claimed_when_the_version_does_not_match():
    A.register(A.tiny_profiles(), "p1", qc_version="9.9.9")
    build("p1", "binned_profile")
    assert sql("SELECT lineage->'source'->>'qc_config_sha256' FROM derived_product")[0][0] is None


def test_the_product_values_in_the_store_are_the_hand_computed_ones():
    import numpy as np
    from scats_store import S3Store, read_store
    A.register(A.tiny_profiles(), "p1")
    res = build("p1", "binned_profile")
    ds = read_store(S3Store.from_env(), "derived", "binned_profile/p1/v1.zarr", expected_manifest_sha256=res.manifest_sha256)
    # the default bins are [0,5) [5,15) [15,25) ...; profile 0 has depths 1, 2 (flags 1, 2 -> 11) and 6 (flag 1 -> 20; the flag-3 value at depth 7 is out)
    assert ds["temperature"].values[0, :2].tolist() == [11.0, 20.0] and ds["temperature_n_obs"].values[0, :2].tolist() == [2, 1]
    assert np.isnan(ds["temperature"].values[2]).all()                      # profile C has a bad position flag


# ------------------------------------------------------------ determinism --
@pytest.mark.parametrize("kind,maker", [("binned_profile", A.tiny_profiles), ("series_hourly", A.tiny_series), ("series_daily", A.tiny_series), ("sst_monthly_mean", A.tiny_sst)])
def test_the_same_inputs_and_code_give_the_same_checksums(kind, maker):
    A.register(maker(), "d1")
    a = build("d1", kind)
    b = build("d1", kind, force=True)
    assert (a.version, b.version) == (1, 2) and a.content_sha256 == b.content_sha256 and a.manifest_sha256 == b.manifest_sha256
    from scats_store import S3Store
    s3 = S3Store.from_env()
    one = {k.split("/", 3)[3]: v for k, v in s3.list("derived", f"{kind}/d1/v1.zarr/").items()}
    two = {k.split("/", 3)[3]: v for k, v in s3.list("derived", f"{kind}/d1/v2.zarr/").items()}
    assert one == two and len(one) > 3


def test_a_changed_configuration_changes_the_product_and_the_recorded_config(tmp_path):
    from scats_products import load_config
    A.register(A.tiny_profiles(), "p1")
    a = build("p1", "binned_profile")
    text = (load_config().raw and open(__import__("scats_products").config.CONFIG_PATH).read()).replace("min_obs: 1                        # good values", "min_obs: 2                        # good values")
    p = tmp_path / "c.yaml"
    p.write_text(text)
    b = build("p1", "binned_profile", config=load_config(p))
    assert b.status == "built" and b.version == 2 and a.content_sha256 != b.content_sha256
    assert sql("SELECT config_sha256 FROM derived_product ORDER BY version")[0][0] != sql("SELECT config_sha256 FROM derived_product ORDER BY version")[1][0]


def test_a_bad_configuration_is_refused(tmp_path):
    from scats_products import load_config
    for old, new in (("[0, 5, 15,", "[0, 15, 5,"), ("good_flags: [1, 2]", "good_flags: [1, 2, 3]")):
        text = open(__import__("scats_products").config.CONFIG_PATH).read().replace(old, new)
        p = tmp_path / "bad.yaml"
        p.write_text(text)
        with pytest.raises(ValueError):
            load_config(p)


# --------------------------------------------------- versions and rebuild --
def test_rebuilding_with_nothing_changed_does_nothing():
    A.register(A.tiny_profiles(), "p1")
    first = build("p1", "binned_profile")
    again = build("p1", "binned_profile")
    assert again.status == "unchanged" and again.version == 1 and again.content_sha256 == first.content_sha256
    assert len(rows()) == 1 and sql("SELECT count(*) FROM audit_log WHERE action = 'product.build'")[0][0] == 1


def test_a_rebuild_supersedes_the_old_version_and_erases_nothing():
    A.register(A.tiny_profiles(), "p1")
    build("p1", "binned_profile")
    objs1 = derived_objects("binned_profile/p1/v1.zarr/")
    build("p1", "binned_profile", force=True)
    assert [(r[1], r[2]) for r in rows()] == [(1, True), (2, False)]                  # v1 superseded, v2 current
    assert derived_objects("binned_profile/p1/v1.zarr/") == objs1 and derived_objects("binned_profile/p1/v2.zarr/")
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'product.build'")[0][0] == 2


def test_a_rebuild_from_a_store_that_no_longer_matches_the_catalogue_is_refused():
    src = A.register(A.tiny_profiles(), "p1")
    build("p1", "binned_profile")
    sql("UPDATE dataset_store SET manifest_sha256 = 'republished', review_version = 8 WHERE dataset_id = %s", (src["dataset_id"],), fetch=False)
    assert sql("SELECT stale FROM catalogue_product")[0][0] is True
    # the store row now names a manifest that does not match the real store, so the rebuild is refused rather than built from it
    from scats_products import ProductError
    with pytest.raises(ProductError):
        build("p1", "binned_profile")
    assert [r[:3] for r in rows()] == [("binned_profile__p1", 1, False)] and sql("SELECT stale FROM catalogue_product")[0][0] is True


def test_a_rebuild_after_a_real_republish_clears_the_stale_mark():
    from scats_store import S3Store, for_zarr, publish_zarr
    src = A.register(A.tiny_profiles(), "p1", review_version=7)
    build("p1", "binned_profile")
    ds = A.tiny_profiles()
    ds["temperature"] = ds["temperature"] + 1
    res = publish_zarr(for_zarr(ds, {"scats_review_version": 8}), s3=S3Store.from_env(), bucket="standardised", platform_type="argo_float", dataset_key="p1")
    sql("UPDATE dataset_store SET manifest_sha256 = %s, content_sha256 = %s, review_version = 8 WHERE dataset_id = %s",
        (res.manifest_sha256, res.content_sha256, src["dataset_id"]), fetch=False)
    assert sql("SELECT version, stale FROM catalogue_product")[0] == (1, True)
    out = build("p1", "binned_profile")
    assert out.status == "built" and out.version == 2
    assert sql("SELECT version, stale, superseded_at IS NOT NULL FROM catalogue_product ORDER BY version") == [(1, True, True), (2, False, False)]
    assert sql("SELECT lineage->'source'->>'review_version' FROM derived_product WHERE version = 2")[0][0] == "8"


# ------------------------------------------------------------- safe write --
def test_a_failed_upload_leaves_the_catalogue_and_the_store_untouched(monkeypatch):
    from scats_products import ProductError
    from scats_store import S3Store
    A.register(A.tiny_profiles(), "p1")
    build("p1", "binned_profile")
    before_rows, before_objs = rows(), derived_objects()
    calls = {"n": 0}
    real = S3Store.put_file

    def flaky(self, bucket, key, path):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return real(self, bucket, key, path)
    monkeypatch.setattr(S3Store, "put_file", flaky)
    with pytest.raises(ProductError) as e:
        build("p1", "binned_profile", force=True)
    assert "upload" in str(e.value) and "nothing published" in str(e.value)
    assert rows() == before_rows and derived_objects() == before_objs          # not even a temporary prefix is left behind


def test_a_corrupted_upload_is_caught_before_anything_is_catalogued(monkeypatch):
    from scats_products import ProductError
    from scats_store import S3Store
    A.register(A.tiny_sst(), "g1")
    real = S3Store.sha256
    monkeypatch.setattr(S3Store, "sha256", lambda self, b, k: ("0" * 64, 1) if "/.tmp/" in "/" + k else real(self, b, k))
    with pytest.raises(ProductError) as e:
        build("g1", "sst_monthly_mean")
    assert "verify_upload" in str(e.value) and rows() == [] and derived_objects() == {}


def test_a_catalogue_failure_removes_what_was_written_and_keeps_the_old_version(monkeypatch):
    from scats_products import ProductError, pipeline
    A.register(A.tiny_profiles(), "p1")
    build("p1", "binned_profile")
    before = derived_objects()
    monkeypatch.setattr(pipeline, "_catalogue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database went away")))
    with pytest.raises(ProductError) as e:
        build("p1", "binned_profile", force=True)
    assert "could not be catalogued" in str(e.value) and "removed" in str(e.value)
    assert derived_objects() == before and [(r[1], r[2]) for r in rows()] == [(1, False)]


def test_a_source_that_is_republished_during_the_build_is_not_catalogued(monkeypatch):
    from scats_products import ProductError, pipeline
    src = A.register(A.tiny_profiles(), "p1")
    real = pipeline.write_zarr_safe

    def write_then_republish(*a, **k):
        out = real(*a, **k)
        sql("UPDATE dataset_store SET review_version = 99 WHERE dataset_id = %s", (src["dataset_id"],), fetch=False)
        return out
    monkeypatch.setattr(pipeline, "write_zarr_safe", write_then_republish)
    with pytest.raises(ProductError) as e:
        build("p1", "binned_profile")
    assert "published again while the product was being built" in str(e.value)
    assert rows() == [] and derived_objects() == {}


def test_two_builds_racing_for_the_same_version_do_not_both_win(monkeypatch):
    from scats_products import ProductError, pipeline
    A.register(A.tiny_profiles(), "p1")
    real = pipeline.write_zarr_safe

    def slow(*a, **k):
        out = real(*a, **k)
        monkeypatch.setattr(pipeline, "write_zarr_safe", real)
        build("p1", "binned_profile", force=True)                 # the other builder finishes first and takes version 1
        return out
    monkeypatch.setattr(pipeline, "write_zarr_safe", slow)
    with pytest.raises(ProductError) as e:
        build("p1", "binned_profile", force=True)
    assert "another build" in str(e.value)
    assert [(r[1], r[2]) for r in rows()] == [(1, False)]


def test_an_old_version_is_never_overwritten_by_a_failed_rebuild(monkeypatch):
    from scats_store import S3Store
    A.register(A.tiny_profiles(), "p1")
    build("p1", "binned_profile")
    before = {k: v for k, v in derived_objects("binned_profile/p1/v1.zarr/").items()}
    monkeypatch.setattr(S3Store, "copy", lambda *a, **k: (_ for _ in ()).throw(OSError("copy failed")))
    from scats_products import ProductError
    with pytest.raises(ProductError):
        build("p1", "binned_profile", force=True)
    assert derived_objects("binned_profile/p1/v1.zarr/") == before and not derived_objects("binned_profile/p1/v2.zarr/")
