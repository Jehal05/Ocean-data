"""Zarr store: round trip for every data type, safe-write behaviour, chunking. moto S3, no database."""
import warnings
from pathlib import Path

import boto3
import numpy as np
import pytest
from moto import mock_aws

from scats_parsers import parse_file
from scats_qc import run_qc
from scats_store import (MANIFEST_NAME, S3Store, StoreCorrupt, apply_human_flags, chunks_for, content_sha256, datasets_equal,
                         for_zarr, publish_zarr, read_store)
from scats_store.layout import MAX_CHUNK_BYTES, TARGET_CHUNK_BYTES

warnings.filterwarnings("ignore")
RAW = Path(__file__).resolve().parents[2] / "data/raw"
FILES = {
    "argo": RAW / "argo_profiles/argo_synthetic_arabian_sea_2025Q1.nc",
    "ctd": RAW / "ship_ctd/ctd_casts_synthetic_arabian_sea_2025.csv",
    "buoy": RAW / "buoy_met_wave/buoy_met_wave_synthetic_2025Q1.csv",
    "sst grid": RAW / "sst_grid/sst_synthetic_0p25deg_2025Q1.nc",
    "bathymetry grid": RAW / "bathymetry/bathymetry_synthetic_0p1deg.nc",
}
ERR = Path(__file__).resolve().parents[2] / "data/test_errors"


@pytest.fixture
def s3():
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="standardised")
        yield S3Store(None, "x", "y")


def prepared(path):
    ds = parse_file(path)
    run_qc(ds, reference_time="2026-01-01T00:00:00Z")
    return ds


def keys(bucket="standardised"):
    return sorted(o["Key"] for o in boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=bucket).get("Contents", []))


@pytest.mark.parametrize("name", list(FILES))
def test_round_trip_is_exact_for_every_data_type(s3, name):
    ds = prepared(FILES[name])
    z = for_zarr(ds, {"scats_review_decision": "approved"})
    res = publish_zarr(z, s3=s3, bucket="standardised", platform_type=ds.attrs["platform_type"], dataset_key="k")
    back = read_store(s3, "standardised", res.prefix, expected_manifest_sha256=res.manifest_sha256)
    assert datasets_equal(z, back) == [] and content_sha256(z) == content_sha256(back) == res.content_sha256
    assert res.zarr_format == 2 and res.uri == f"s3://standardised/{ds.attrs['platform_type']}/k.zarr"
    assert back.attrs["scats_review_decision"] == "approved" and back.attrs["dataset_type"] == ds.attrs["dataset_type"]
    # flags are stored for every data variable, as integers, unchanged
    for n in ds.data_vars:
        if n.endswith("_qc"):
            assert back[n].dtype == ds[n].dtype and np.array_equal(back[n].values, ds[n].values)
    assert not [k for k in keys() if k.startswith(".tmp/")], "temporary prefix must be removed"


def test_missing_values_and_times_survive(s3):
    ds = prepared(ERR / "buoy_met_wave_errors.csv")                         # NaN values and flag 9 cells
    z = for_zarr(ds, {})
    res = publish_zarr(z, s3=s3, bucket="standardised", platform_type="moored_buoy", dataset_key="b")
    back = read_store(s3, "standardised", res.prefix)
    names = [n for n in ds.data_vars if not n.endswith("_qc") and ds[n].dtype.kind == "f"]
    assert sum(int(np.isnan(ds[n].values).sum()) for n in names) > 0                       # the file really has missing values
    for n in names:
        assert np.array_equal(np.isnan(back[n].values), np.isnan(ds[n].values))           # and they are still missing, same places
    assert datasets_equal(z, back) == [] and (back["time"].values == ds["time"].values).all()


def test_compression_is_lossless_and_zarr_v2(s3):
    import json
    ds = prepared(FILES["buoy"])
    publish_zarr(for_zarr(ds, {}), s3=s3, bucket="standardised", platform_type="moored_buoy", dataset_key="b")
    meta = json.loads(s3.get_bytes("standardised", "moored_buoy/b.zarr/wind_speed/.zarray"))
    assert meta["zarr_format"] == 2 and meta["compressor"]["id"] == "blosc" and meta["compressor"]["cname"] == "zstd"
    assert meta["filters"] in (None, [])                                      # no quantising filter: lossless
    man = json.loads(s3.get_bytes("standardised", f"moored_buoy/b.zarr/{MANIFEST_NAME}"))
    assert man["zarr_format"] == 2 and man["objects"] and man["content_sha256"]


def test_human_flags_change_only_the_flag_arrays():
    ds = prepared(ERR / "buoy_met_wave_errors.csv")
    rec = int(np.nonzero(ds["relative_humidity_qc"].values == 4)[0][0])
    out = apply_human_flags(ds, [(rec, "relative_humidity", -1, 1)])
    assert int(out["relative_humidity_qc"].values[rec]) == 1 and int(ds["relative_humidity_qc"].values[rec]) == 4   # source untouched
    diffs = datasets_equal(ds, out)
    assert diffs == ["relative_humidity_qc: values differ"]
    pos = apply_human_flags(ds, [(3, "position", -1, 2)])
    assert int(pos["position_qc"].values[3]) == 2


def test_human_flags_on_profile_levels_and_grids():
    ds = prepared(ERR / "ctd_casts_errors.csv")
    out = apply_human_flags(ds, [(2, "temperature", 5, 4), (2, "position", -1, 3)])
    assert int(out["temperature_qc"].values[2, 5]) == 4 and int(out["position_qc"].values[2]) == 3
    grid = prepared(FILES["sst grid"])
    assert datasets_equal(grid, apply_human_flags(grid, [(0, "sea_surface_temperature", -1, 4)])) == []   # grids take no cell decisions


def test_attributes_are_json_safe():
    ds = prepared(FILES["argo"])
    ds.attrs["odd"] = np.float64("nan")
    ds.attrs["arr"] = np.arange(3)
    z = for_zarr(ds, {})
    import json
    json.dumps(z.attrs, allow_nan=False)
    assert z.attrs["odd"] == "nan" and z.attrs["arr"] == [0, 1, 2]


def test_chunks_follow_the_access_pattern_and_respect_the_cap():
    assert chunks_for("profile", ("profile", "level"), (60, 80), 8) == (60, 80)
    big = chunks_for("profile", ("profile", "level"), (1_000_000, 2000), 8)
    assert big[1] == 2000 and big[0] * 2000 * 8 <= TARGET_CHUNK_BYTES
    ts = chunks_for("timeseries", ("record",), (50_000_000,), 8)
    assert ts == (TARGET_CHUNK_BYTES // 8,)
    g = chunks_for("grid", ("time", "latitude", "longitude"), (365, 4000, 8000), 8)
    assert g[1] == 512 and g[2] == 512 and np.prod(g) * 8 <= MAX_CHUNK_BYTES
    assert chunks_for("bathymetry", ("latitude", "longitude"), (100000, 100000), 4) == (512, 512)
    with pytest.raises(ValueError):
        chunks_for("other", ("a", "b"), (10_000_000, 1000), 8)


def test_a_chunk_never_exceeds_the_cap_in_a_real_write(s3):
    import json
    ds = prepared(FILES["sst grid"])
    publish_zarr(for_zarr(ds, {}), s3=s3, bucket="standardised", platform_type="satellite", dataset_key="g")
    m = json.loads(s3.get_bytes("standardised", "satellite/g.zarr/sea_surface_temperature/.zarray"))
    assert np.prod(m["chunks"]) * 8 <= MAX_CHUNK_BYTES and m["chunks"][1:] == [65, 61]


def test_a_failed_upload_leaves_the_final_prefix_empty_and_cleans_up(s3, monkeypatch):
    from scats_store import PublishError
    ds = for_zarr(prepared(FILES["bathymetry grid"]), {})
    n = {"i": 0}
    real = S3Store.put_file

    def flaky(self, b, k, p):
        n["i"] += 1
        if n["i"] == 3:
            raise ConnectionError("down")
        return real(self, b, k, p)

    monkeypatch.setattr(S3Store, "put_file", flaky)
    with pytest.raises(PublishError) as e:
        publish_zarr(ds, s3=s3, bucket="standardised", platform_type="bathymetry_grid", dataset_key="x")
    assert e.value.stage == "upload" and "nothing published" in str(e.value) and keys() == []


def test_stale_objects_from_an_earlier_failed_attempt_are_replaced_not_mixed_in(s3):
    ds = for_zarr(prepared(FILES["bathymetry grid"]), {})
    s3.put_bytes("standardised", "bathymetry_grid/x.zarr/leftover-from-a-crash", b"junk")
    res = publish_zarr(ds, s3=s3, bucket="standardised", platform_type="bathymetry_grid", dataset_key="x")
    assert "bathymetry_grid/x.zarr/leftover-from-a-crash" not in keys()
    assert datasets_equal(ds, read_store(s3, "standardised", res.prefix)) == []


def test_reader_rejects_a_changed_extra_or_foreign_object(s3):
    ds = for_zarr(prepared(FILES["bathymetry grid"]), {})
    res = publish_zarr(ds, s3=s3, bucket="standardised", platform_type="bathymetry_grid", dataset_key="x")
    s3.put_bytes("standardised", f"{res.prefix}/extra-object", b"extra")
    with pytest.raises(StoreCorrupt, match="not in the manifest"):
        read_store(s3, "standardised", res.prefix)
    s3.delete_prefix("standardised", f"{res.prefix}/extra-object")
    with pytest.raises(StoreCorrupt, match="catalogue"):
        read_store(s3, "standardised", res.prefix, expected_manifest_sha256="0" * 64)
    s3.put_bytes("standardised", f"{res.prefix}/.zgroup", b"{}")
    with pytest.raises(StoreCorrupt, match="manifest"):
        read_store(s3, "standardised", res.prefix)
