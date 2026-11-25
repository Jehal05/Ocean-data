"""Phase 4 test world: stand-in PostgreSQL, moto S3 (buckets standardised and derived), the review API to publish
datasets, then products and the access API on top. Everything goes through the real code paths."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import boto3

import db_stub
from review_helpers import RAW, set_env, sql

ARGO = RAW / "argo_profiles/argo_synthetic_arabian_sea_2025Q1.nc"
CTD = RAW / "ship_ctd/ctd_casts_synthetic_arabian_sea_2025.csv"
BUOY = RAW / "buoy_met_wave/buoy_met_wave_synthetic_2025Q1.csv"
SST = RAW / "sst_grid/sst_synthetic_0p25deg_2025Q1.nc"
BATHY = RAW / "bathymetry/bathymetry_synthetic_0p1deg.nc"

def key_of(path: Path) -> str:
    from scats_ingest.service import dataset_key_for
    return dataset_key_for(path)


def make_buckets():
    c = boto3.client("s3", region_name="us-east-1")
    for b in ("raw", "standardised", "derived"):
        c.create_bucket(Bucket=b)


def reviewer_client(tmp: Path, monkeypatch):
    """A TestClient of the review API plus the login headers of the three seed users."""
    from fastapi.testclient import TestClient
    from review_api.main import app
    from review_helpers import login
    set_env(monkeypatch, tmp / "staging", REVIEW_FOUR_EYES="true")
    client = TestClient(app, raise_server_exceptions=False)
    return SimpleNamespace(client=client, admin=login(client, "admin"), reviewer=login(client, "reviewer"), viewer=login(client, "viewer"))


def publish(world, path: Path) -> str:
    """Ingest a file, start the review, approve it (which publishes the Zarr). Returns the dataset key."""
    from review_helpers import ingest
    key = key_of(path)
    ingest(path, world.staging)
    c, h = world.review.client, world.review.reviewer
    r = c.post(f"/datasets/{key}/review/start", headers=h)
    assert r.status_code == 200, r.text
    r = c.post(f"/datasets/{key}/review", headers=h, json={"decision": "approved", "comment": "ok", "accept_unreviewed_flag4": True})
    assert r.status_code == 200, r.text
    assert sql("SELECT status FROM dataset WHERE dataset_key = %s", (key,))[0][0] == "published", r.text
    return key


def set_products_env(monkeypatch):
    monkeypatch.setenv("PRODUCTS_DATABASE_URL", db_stub.PRODUCTS_URL)
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")


# ------------------------------------------------------------------ tiny datasets, registered without the review flow --
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

NAN = np.nan


def _attrs(dataset_type, platform_type, lat=(10.0, 12.0), lon=(65.0, 67.0), t=("2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"), **extra):
    return {"title": f"tiny {dataset_type}", "dataset_type": dataset_type, "platform_type": platform_type, "platform_id": "T1", "source": "test",
            "instrument": "test", "is_synthetic": "true", "license": "test", "Conventions": "CF-1.10, SCATS-0.1", "processing_level": "standardised",
            "geospatial_lat_min": str(lat[0]), "geospatial_lat_max": str(lat[1]), "geospatial_lon_min": str(lon[0]), "geospatial_lon_max": str(lon[1]),
            "time_coverage_start": t[0], "time_coverage_end": t[1], **extra}


def tiny_profiles(key="tiny_profiles"):
    depth = np.array([[1, 2, 6, 7, 100], [1, 3, 6, 20, NAN], [1, 2, 6, 7, 100]], float)
    ds = xr.Dataset({
        "temperature": (("profile", "level"), np.array([[10, 12, 20, 22, 5], [11, 13, 21, 23, NAN], [10, 12, 20, 22, 5]], float), {"units": "degC"}),
        "temperature_qc": (("profile", "level"), np.array([[1, 2, 1, 3, 1], [1, 4, 2, 1, 9], [1, 2, 1, 3, 1]], "int8")),
        "depth": (("profile", "level"), depth, {"units": "m"}),
        "time": ("profile", np.array(["2025-01-01", "2025-01-02", "2025-01-03"], "datetime64[ns]")),
        "latitude": ("profile", [10.0, 11.0, 12.0], {"units": "degrees_north"}), "longitude": ("profile", [65.0, 66.0, 67.0], {"units": "degrees_east"}),
        "platform_id": ("profile", np.array(["A", "B", "C"], dtype=object)), "source_record": ("profile", [0, 1, 2]),
        "position_qc": ("profile", np.array([1, 1, 4], "int8")), "time_qc": ("profile", np.ones(3, "int8"))})
    ds.attrs = _attrs("profile", "argo_float", t=("2025-01-01T00:00:00Z", "2025-01-03T00:00:00Z"))
    return ds


def tiny_series(key="tiny_series", platform="=cmd|' /C calc'!A0"):
    t = np.array(["2025-01-01T00:10", "2025-01-01T00:50", "2025-01-01T01:20", "2025-01-01T03:05", "2025-01-01T00:30", "2025-01-01T01:40"], "datetime64[ns]")
    ds = xr.Dataset({
        "wind_speed": ("record", np.array([2, 4, 6, 8, 100, 50], float), {"units": "m s-1"}),
        "wind_speed_qc": ("record", np.array([1, 1, 1, 1, 3, 1], "int8")),
        "wind_direction": ("record", np.array([350, 10, 90, 180, 0, 0], float), {"units": "degree"}),
        "wind_direction_qc": ("record", np.ones(6, "int8")),
        "time": ("record", t), "latitude": ("record", [15.0] * 6, {"units": "degrees_north"}), "longitude": ("record", [68.0] * 6, {"units": "degrees_east"}),
        "platform_id": ("record", np.array([platform] * 6, dtype=object)), "source_record": ("record", np.arange(6)),
        "position_qc": ("record", np.ones(6, "int8")), "time_qc": ("record", np.array([1, 1, 1, 1, 1, 4], "int8"))})
    ds.attrs = _attrs("timeseries", "moored_buoy", lat=(15.0, 15.0), lon=(68.0, 68.0), t=("2025-01-01T00:10:00Z", "2025-01-01T03:05:00Z"))
    return ds


def tiny_sst(lon=(65.0, 65.25), key="tiny_sst"):
    t = np.array(["2025-01-30", "2025-01-31", "2025-02-01", "2025-02-02"], "datetime64[ns]")
    v = np.array([[[1, 10], [5, 5]], [[2, NAN], [5, 5]], [[3, 30], [5, 5]], [[4, 40], [5, 5]]], float)
    q = np.ones((4, 2, 2), "int8")
    q[3, 0, 1] = 3
    ds = xr.Dataset({"sea_surface_temperature": (("time", "latitude", "longitude"), v, {"units": "degC"}),
                     "sea_surface_temperature_qc": (("time", "latitude", "longitude"), q), "time_qc": ("time", np.ones(4, "int8"))},
                    coords={"time": t, "latitude": ("latitude", [10.0, 10.25], {"units": "degrees_north"}), "longitude": ("longitude", list(lon), {"units": "degrees_east"})})
    ds.attrs = _attrs("grid", "satellite", lat=(10.0, 10.25), lon=(min(lon), max(lon)), t=("2025-01-30T00:00:00Z", "2025-02-02T00:00:00Z"))
    return ds


def register(ds, key, *, status="published", store=True, review_version=7, qc_version="0.2.0"):
    """Insert a dataset (and, if wanted, its Zarr store, review row and dataset_store row) as the superuser, bypassing the review flow."""
    from scats_store import S3Store, for_zarr, publish_zarr
    a = ds.attrs
    did = sql("INSERT INTO dataset (dataset_key, title, dataset_type, source, is_synthetic, license, time_start, time_end, zone, storage_uri, original_format,"
              " checksum_sha256, conventions, attributes, status) VALUES (%s,%s,%s,%s,true,%s,%s,%s,'raw',%s,'nc','x',%s,%s::jsonb,%s) RETURNING dataset_id",
              (key, a["title"], a["dataset_type"], a["source"], a["license"], a["time_coverage_start"], a["time_coverage_end"], f"file:///nonexistent/{key}",
               a["Conventions"], __import__("json").dumps(a), status))[0][0]
    for n in ds.data_vars:
        if f"{n}_qc" in ds.variables and n not in ("position", "time"):
            sql("INSERT INTO dataset_variable (dataset_id, variable_name, units) VALUES (%s,%s,%s)", (did, n, ds[n].attrs.get("units", "1")), fetch=False)
    out = {"dataset_id": did, "key": key}
    if store:
        rid = sql("INSERT INTO review (dataset_id, reviewer_id, decision, comment, decided_at, review_version) VALUES (%s, 2, 'approved', 'ok', now(), %s) RETURNING review_id",
                  (did, review_version))[0][0]
        fz = for_zarr(ds, {"scats_review_version": review_version, "scats_qc_config_version": qc_version})
        res = publish_zarr(fz, s3=S3Store.from_env(), bucket="standardised", platform_type=a["platform_type"], dataset_key=key)
        sid = sql("INSERT INTO dataset_store (dataset_id, review_id, zone, store_uri, zarr_format, content_sha256, manifest_sha256, n_objects, total_bytes,"
                  " review_version, qc_config_version) VALUES (%s,%s,'standardised',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING store_id",
                  (did, rid, res.uri, res.zarr_format, res.content_sha256, res.manifest_sha256, res.n_objects, res.total_bytes, review_version, qc_version))[0][0]
        out.update(store_id=sid, review_id=rid, uri=res.uri, manifest_sha256=res.manifest_sha256, content_sha256=res.content_sha256, prefix=res.prefix)
    return out
