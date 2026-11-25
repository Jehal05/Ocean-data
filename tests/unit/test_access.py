"""Filters, row selection, outputs and the lazy store, without a database."""
import numpy as np
import pytest
import xarray as xr
from moto import mock_aws
from starlette.datastructures import QueryParams

from access_api import engine, outputs
from access_api.filters import Bbox, Filter, Refused, parse_bbox, parse_filter, read_params

VARS = ["temperature", "salinity"]


def parse(q: str, **kw):
    args = dict(variables=VARS, has_time=True, has_depth=True, has_platform=True, is_product=False)
    args.update(kw)
    return parse_filter(read_params(QueryParams(q)), **args)


# ------------------------------------------------------------------ filters --
@pytest.mark.parametrize("q", [
    "bbox=1,2,3", "bbox=a,b,c,d", "bbox=1,2,3,nan", "bbox=1,2,3,inf", "bbox=1,2,3,1e999", "bbox=0,-91,10,10", "bbox=0,0,10,91",
    "bbox=0,20,10,10",                                   # south greater than north
    "bbox=1_0,0,20,10", "bbox=0x10,0,20,10", "bbox=0,0,10,10;DROP", "bbox=999999,0,1,1",
    "depth_min=-5", "depth_min=abc", "depth_min=nan", "depth_min=1e9", "depth_min=20&depth_max=10",
    "time_from=2025-13-01", "time_from=2025-02-30", "time_from=yesterday", "time_from=1800-01-01", "time_from=2025-01-01';--",
    "time_from=2025-02-01&time_to=2025-01-01", "variable=temperature;DROP", "variable=nosuch", "variable=../../etc/passwd",
    "variable=temperature%00", "platform=a b", "platform=../x", "platform=" + "x" * 70, "quality=bad", "quality=GOOD",
    "page=-1&x=1", "unknown=1", "variable=temperature&variable=salinity",
])
def test_hostile_or_malformed_filters_are_refused(q):
    with pytest.raises(Refused) as e:
        parse(q)
        # page values are read later, with the limits
        from access_api.filters import integer
        if "page=" in q:
            integer(read_params(QueryParams(q))["page"], "page", 1, 10)
    assert e.value.status == 422


def test_control_characters_and_long_values_are_refused():
    with pytest.raises(Refused):
        read_params(QueryParams({"variable": "temperature\x00"}))
    with pytest.raises(Refused):
        read_params(QueryParams({"platform": "a" * 401}))


def test_good_filters_are_applied_as_given():
    f = parse("time_from=2025-01-01&time_to=2025-01-02&bbox=60,5,70,15&depth_min=0&depth_max=100&variable=temperature,salinity&platform=A1,B-2&quality=all")
    assert f.time_from == np.datetime64("2025-01-01T00:00:00", "ns") and f.time_to == np.datetime64("2025-01-02T23:59:59.999999999", "ns")
    assert f.bbox == Bbox(60, 5, 70, 15) and f.variables == ("temperature", "salinity") and f.platforms == ("A1", "B-2") and f.quality == "all"


def test_a_filter_that_does_not_fit_the_collection_is_refused():
    with pytest.raises(Refused):
        parse("depth_max=10", has_depth=False)
    with pytest.raises(Refused):
        parse("time_from=2025-01-01", has_time=False)
    with pytest.raises(Refused):
        parse("platform=A", has_platform=False)
    with pytest.raises(Refused) as e:
        parse("quality=all", is_product=True)
    assert "products hold only good values" in e.value.message


def test_longitude_wraps_at_the_antimeridian():
    b = parse_bbox("170,0,-170,10")
    assert b.wraps and b.width == 20.0 and b.area == 200.0
    lon = np.array([160, 170, 175, 180, -180, -175, -170, -169, 0.0])
    assert b.lon_mask(lon).tolist() == [False, True, True, True, True, True, True, False, False]
    assert parse_bbox("-180,0,180,10").full_lon and parse_bbox("0,0,360,10").width == 360
    assert parse_bbox("350,0,10,5") == Bbox(-10, 0, 10, 5)            # 350 east is 10 west
    assert not parse_bbox("10,0,180,5").wraps and parse_bbox("10,0,180,5").lon_mask(np.array([179.0, -179.0])).tolist() == [True, False]


# --------------------------------------------------------------- selection --
def grid_lon_near_180():
    lon = np.array([160.0, 170.0, 180.0, -170.0, -160.0])
    return xr.Dataset({"sst": (("time", "latitude", "longitude"), np.arange(2 * 3 * 5, dtype=float).reshape(2, 3, 5)),
                       "sst_qc": (("time", "latitude", "longitude"), np.ones((2, 3, 5), "int8")), "time_qc": ("time", np.ones(2, "int8"))},
                      coords={"time": np.array(["2025-01-01", "2025-01-02"], "datetime64[ns]"), "latitude": [0.0, 5.0, 10.0], "longitude": lon})


def test_a_box_across_180_selects_the_right_columns():
    ds = grid_lon_near_180()
    plan = engine.make_plan(ds, Filter(bbox=parse_bbox("170,0,-170,10"), quality="all"), is_product=False)
    assert plan.index["longitude"].tolist() == [1, 2, 3] and plan.index["latitude"].tolist() == [0, 1, 2] and plan.n_rows == 2 * 3 * 3
    df = engine.table(ds, plan, 0, plan.n_rows)
    assert sorted(df["longitude"].unique().tolist()) == [-170.0, 170.0, 180.0]
    assert df["sst"].iloc[0] == ds["sst"].values[0, 0, 1]


def test_an_empty_selection_is_a_valid_answer():
    ds = grid_lon_near_180()
    plan = engine.make_plan(ds, Filter(bbox=parse_bbox("10,0,20,10"), quality="all"), is_product=False)
    df = engine.table(ds, plan, 0, plan.n_rows)
    assert plan.n_rows == 0 and len(df) == 0 and list(df.columns)[:3] == ["time", "latitude", "longitude"]


def profile_ds():
    depth = np.array([[1, 2, 3, np.nan], [1, 2, np.nan, np.nan]], float)
    return xr.Dataset({"temperature": (("profile", "level"), np.array([[10, 11, 12, np.nan], [20, 21, np.nan, np.nan]], float), {"units": "degC"}),
                       "temperature_qc": (("profile", "level"), np.array([[1, 3, 1, 9], [1, 1, 9, 9]], "int8")),
                       "depth": (("profile", "level"), depth), "time": ("profile", np.array(["2025-01-01", "2025-01-05"], "datetime64[ns]")),
                       "latitude": ("profile", [10.0, 12.0]), "longitude": ("profile", [65.0, 66.0]),
                       "platform_id": ("profile", np.array(["A", "B"])), "source_record": ("profile", [0, 1]),
                       "position_qc": ("profile", np.array([1, 4], "int8")), "time_qc": ("profile", np.array([1, 1], "int8"))})


def test_good_quality_hides_flagged_values_and_bad_positions():
    ds = profile_ds()
    plan = engine.make_plan(ds, Filter(quality="good"), is_product=False)
    df = engine.table(ds, plan, 0, plan.n_rows)
    assert plan.n_rows == 3 and df["platform_id"].unique().tolist() == ["A"]            # profile B has a bad position flag
    assert df["temperature"].tolist()[0] == 10.0 and np.isnan(df["temperature"].tolist()[1]) and df["temperature_qc"].tolist() == [1, 3, 1]
    allq = engine.make_plan(ds, Filter(quality="all"), is_product=False)
    assert allq.n_rows == 5 and engine.table(ds, allq, 0, 5)["temperature"].tolist() == [10.0, 11.0, 12.0, 20.0, 21.0]


def test_depth_platform_and_time_filters_and_paging():
    ds = profile_ds()
    f = Filter(depth_min=2, depth_max=3, platforms=("A",), time_to=np.datetime64("2025-01-02", "ns"), quality="all")
    plan = engine.make_plan(ds, f, is_product=False)
    assert plan.n_rows == 2 and engine.table(ds, plan, 1, 2)["depth"].tolist() == [3.0]


# ----------------------------------------------------------------- outputs --
def test_csv_cells_that_look_like_formulas_are_neutralised_but_numbers_are_not():
    import pandas as pd
    df = pd.DataFrame({"platform_id": ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx", "normal"], "v": [-1.5, 2.0, np.nan, 4.0, -0.0, 6.0]})
    text = outputs.csv_bytes(df).decode()
    lines = text.splitlines()
    assert lines[1].startswith("'=1+1,-1.5") and lines[2].startswith("'+cmd") and lines[3].startswith("'-2+3,") and lines[4].startswith("'@SUM")
    assert lines[5].startswith("'\t") and lines[6].startswith("normal,6.0") and lines[3].endswith(",")      # NaN is an empty cell, not 'nan'


def test_json_has_no_nan_and_times_are_iso():
    import pandas as pd
    df = pd.DataFrame({"t": np.array(["2025-01-01T06:30:00"], "datetime64[ns]"), "v": [np.nan], "i": [np.int64(3)]})
    assert outputs.json_rows(df) == [{"t": "2025-01-01T06:30:00Z", "v": None, "i": 3}]


def test_file_names_are_made_by_the_server():
    assert outputs.safe_filename("plain_key-1.0", 3, "nc") == "plain_key-1.0_v3.nc" and outputs.safe_filename("k", None, "csv") == "k.csv"
    name = outputs.safe_filename('../../a b";x\r\n', 2, "csv")
    assert "/" not in name and '"' not in name and " " not in name and "\n" not in name and name.endswith("_v2.csv")


# ---------------------------------------------------------------- lazy store --
@pytest.fixture
def small_chunk_store():
    from scats_store import S3Store, for_zarr, publish_zarr  # noqa: F401
    from scats_store.writer import write_zarr_safe
    import boto3
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="bucket-t")
        n = 1000
        ds = xr.Dataset({"x": ("record", np.arange(n, dtype=float)), "y": ("record", np.arange(n, dtype=float) * 2),
                         "time": ("record", np.arange(n).astype("datetime64[s]").astype("datetime64[ns]"))})
        ds.attrs["dataset_type"] = "timeseries"
        s3 = S3Store(None, "k", "s")
        res = write_zarr_safe(ds, s3=s3, bucket="bucket-t", final="t/x.zarr", chunker=lambda name, dims, shape, size: (100,) if shape else ())
        yield s3, res


def test_a_small_window_reads_only_the_chunks_it_needs(small_chunk_store):
    from scats_store.lazy import open_lazy
    s3, res = small_chunk_store
    ds, store, manifest = open_lazy(s3, "bucket-t", res.prefix, expected_manifest_sha256=res.manifest_sha256)
    total_chunks = [k for k in manifest["objects"] if k.startswith("x/") and not k.startswith("x/.")]
    assert len(total_chunks) == 10
    vals = ds["x"].isel(record=slice(150, 260)).values                  # records 150..259 live in chunks 1, 2 (and 3 covers 300)
    assert vals.tolist() == list(np.arange(150, 260, dtype=float))
    assert sorted(k for k in store.fetched if k.startswith("x/") and not k.startswith("x/.")) == ["x/1", "x/2"]
    assert not any(k.startswith("y/") for k in store.fetched)           # an array nobody asked for is never touched
    assert len(store.fetched) < len(manifest["objects"]) / 2


def test_a_changed_object_or_manifest_is_refused(small_chunk_store):
    from scats_store import StoreCorrupt
    from scats_store.lazy import open_lazy
    s3, res = small_chunk_store
    with pytest.raises(StoreCorrupt):
        open_lazy(s3, "bucket-t", res.prefix, expected_manifest_sha256="0" * 64)
    s3.put_bytes("bucket-t", f"{res.prefix}/x/3", b"not what was written")
    ds, store, _m = open_lazy(s3, "bucket-t", res.prefix, expected_manifest_sha256=res.manifest_sha256)
    assert ds["x"].isel(record=slice(0, 10)).values.tolist() == list(np.arange(10, dtype=float))     # chunk 0 is fine
    with pytest.raises(Exception) as e:
        ds["x"].isel(record=slice(300, 310)).values
    assert "manifest" in str(e.value) or "StoreCorrupt" in repr(e.value)


def test_the_lazy_store_only_serves_keys_in_the_manifest(small_chunk_store):
    import asyncio
    from scats_store.lazy import open_lazy
    from zarr.core.buffer import default_buffer_prototype
    s3, res = small_chunk_store
    _ds, store, _m = open_lazy(s3, "bucket-t", res.prefix, expected_manifest_sha256=res.manifest_sha256)
    before = list(store.fetched)                 # opening the dataset reads the metadata and the time coordinate
    for bad in ("../other/x/0", "/etc/passwd", "x/../../y", "scats_manifest.json", "nope"):
        assert asyncio.run(store.get(bad, default_buffer_prototype())) is None
    assert store.fetched == before               # nothing outside the manifest was ever requested from S3
