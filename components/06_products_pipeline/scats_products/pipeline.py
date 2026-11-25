"""Build derived products from published datasets and record them (docs/phase4_design.md sections 2, 5, 7).

Only a dataset with status `published` and a `dataset_store` row is read, through scats_store.read_store (every object
checked against the manifest). Staging and raw are never touched. A product is written with the same safe write as the
standardised store, then catalogued in one transaction together with its audit row.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import psycopg2
import xarray as xr

from scats_common.qc_config_ref import qc_config_sha256_for
from scats_store import S3Store, StoreCorrupt, content_sha256, for_zarr, read_store, write_zarr_safe
from scats_store.layout import ZARR_FORMAT, chunks_for
from scats_store.writer import PublishError

from . import binning
from .config import ProductsConfig, code_sha256, load_config

CODE_VERSION = "0.1.0"
ACTOR = "scats_products"
SOURCE_BUCKET = "standardised"
DERIVED_BUCKET = "derived"
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,150}$")


class ProductError(RuntimeError):
    """The product was not built. The message says why and what, if anything, was left behind."""


@dataclass
class BuildResult:
    product_key: str
    kind: str
    version: int
    status: str                 # built | unchanged | skipped
    detail: str = ""
    uri: str | None = None
    content_sha256: str | None = None
    manifest_sha256: str | None = None


def kinds_for(dataset_type: str, variables) -> list[str]:
    if dataset_type == "profile":
        return ["binned_profile"]
    if dataset_type == "timeseries":
        return ["series_hourly", "series_daily"]
    if dataset_type == "grid" and "sea_surface_temperature" in variables:
        return ["sst_monthly_mean"]
    return []                   # bathymetry: the standardised Zarr is enough (design section 2)


def _db_url() -> str:
    return os.getenv("PRODUCTS_DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql://scats_products:scats_products_dev_pw@localhost:5432/scats")


def _chunker(ds: xr.Dataset):
    kind = ds.attrs["dataset_type"]

    def chunks(name, dims, shape, itemsize):
        if kind == "timeseries" and len(shape) == 2:                       # (platform, time): one platform, a run of bins
            return (1, max(1, min(shape[1], 2**20 // max(itemsize, 1))))
        return chunks_for(kind, dims, shape, itemsize)
    return chunks


def _make(kind: str, ds: xr.Dataset, cfg: ProductsConfig) -> tuple[xr.Dataset, dict]:
    r, gf = cfg.raw, cfg.good_flags
    if kind == "binned_profile":
        c = r["binned_profile"]
        out = binning.binned_profile(ds, edges=c["depth_bin_edges_m"], variables=c["variables"], good_flags=gf, min_obs=c["min_obs"])
        params = {"depth_bin_edges_m": c["depth_bin_edges_m"], "min_obs": c["min_obs"], "bin_definition": "[top, bottom) in metres of depth"}
        text = "Mean of good values per profile and depth bin; no interpolation."
    elif kind in ("series_hourly", "series_daily"):
        unit, key = ("h", "hourly") if kind == "series_hourly" else ("D", "daily")
        mo = r["series"][key]["min_obs"]
        out = binning.binned_series(ds, unit=unit, good_flags=gf, min_obs=mo, circular_units=r["series"]["circular_units"])
        params = {"bin": "UTC hour" if unit == "h" else "UTC day", "min_obs": mo, "circular_units": r["series"]["circular_units"]}
        text = f"Per platform and {params['bin']}: mean, min, max and count of good values (circular mean for directions); no interpolation."
    elif kind == "sst_monthly_mean":
        c = r["sst_monthly_mean"]
        out = binning.monthly_mean(ds, variable=c["variable"], good_flags=gf, min_obs=c["min_obs"])
        params = {"variable": c["variable"], "min_obs": c["min_obs"], "bin": "UTC calendar month", "grid": "source grid, no regridding"}
        text = "Monthly mean of good daily values on the source grid; no regridding, no interpolation."
    else:
        raise ProductError(f"unknown product kind {kind}")
    return out, {"parameters": params, "description": text}


def _extent(ds: xr.Dataset) -> dict:
    out = {}
    t = ds["time"].values
    if t.size:
        import numpy as np
        ok = ~np.isnat(t)
        if ok.any():
            out["time_start"], out["time_end"] = str(t[ok].min().astype("datetime64[s]")) + "Z", str(t[ok].max().astype("datetime64[s]")) + "Z"
    return out


def _source(cur, dataset_key: str) -> dict:
    cur.execute("SELECT d.dataset_id, d.dataset_key, d.dataset_type, d.title, d.status, d.attributes, s.store_id, s.store_uri,"
                " s.manifest_sha256, s.content_sha256, s.review_id, s.review_version, s.qc_config_version, s.published_at,"
                " s.zone FROM dataset d LEFT JOIN dataset_store s ON s.dataset_id = d.dataset_id WHERE d.dataset_key = %s",
                (dataset_key,))
    r = cur.fetchone()
    if r is None:
        raise ProductError(f"dataset '{dataset_key}' does not exist")
    cols = ["dataset_id", "dataset_key", "dataset_type", "title", "status", "attributes", "store_id", "store_uri", "manifest_sha256",
            "content_sha256", "review_id", "review_version", "qc_config_version", "published_at", "zone"]
    d = dict(zip(cols, r))
    if d["status"] != "published" or d["store_id"] is None:
        raise ProductError(f"dataset '{dataset_key}' is '{d['status']}', not 'published': products are built only from published data")
    return d


def _split_uri(uri: str) -> tuple[str, str]:
    m = re.match(r"^s3://([^/]+)/(.+)$", uri)
    if not m:
        raise ProductError(f"cannot read the store address {uri!r}")
    return m.group(1), m.group(2)


def build_product(dataset_key: str, kind: str, *, force: bool = False, db_url: str | None = None, s3: S3Store | None = None,
                  config: ProductsConfig | None = None) -> BuildResult:
    if not KEY_RE.match(dataset_key):
        raise ProductError("not a valid dataset key")
    cfg = config or load_config()
    s3 = s3 or S3Store.from_env()
    url = db_url or _db_url()
    code = code_sha256()
    product_key = f"{kind}__{dataset_key}"
    con = psycopg2.connect(url, connect_timeout=5)
    try:
        with con.cursor() as cur:
            src = _source(cur, dataset_key)
            cur.execute("SELECT product_id, version, content_sha256, manifest_sha256, code_sha256, config_sha256, source_manifest_sha256,"
                        " source_review_version, stale_at FROM derived_product WHERE product_key = %s AND superseded_at IS NULL", (product_key,))
            current = cur.fetchone()
        con.rollback()
        bucket, prefix = _split_uri(src["store_uri"])
        if bucket != SOURCE_BUCKET:
            raise ProductError(f"the source store is in '{bucket}', not '{SOURCE_BUCKET}'")
        try:
            ds = read_store(s3, bucket, prefix, expected_manifest_sha256=src["manifest_sha256"])
        except StoreCorrupt as exc:
            raise ProductError(f"the source Zarr store failed its check and was not used: {exc}") from exc
        if content_sha256(ds) != src["content_sha256"]:
            raise ProductError("the source Zarr content does not match the catalogue checksum")
        if kind not in kinds_for(src["dataset_type"], binning.data_variables(ds)):
            raise ProductError(f"'{kind}' is not defined for a {src['dataset_type']} dataset")
        out, info = _make(kind, ds, cfg)
        lineage = {
            "kind": kind, "product_key": product_key, "code_version": CODE_VERSION, "code_sha256": code, "config_sha256": cfg.sha256,
            "source": {"dataset_key": dataset_key, "dataset_id": src["dataset_id"], "store_uri": src["store_uri"],
                       "store_manifest_sha256": src["manifest_sha256"], "store_content_sha256": src["content_sha256"],
                       "review_id": src["review_id"], "review_version": src["review_version"], "qc_config_version": src["qc_config_version"],
                       "qc_config_sha256": qc_config_sha256_for(src["qc_config_version"]),
                       "published_at": src["published_at"].isoformat() if src["published_at"] else None},
            "flags_used": list(cfg.good_flags), "parameters": info["parameters"]}
        attrs = {"title": f"{kind} of {dataset_key}", "summary": info["description"], "product_kind": kind, "processing_level": "derived",
                 "dataset_type": ds.attrs["dataset_type"], "platform_type": ds.attrs.get("platform_type", ""), "source_dataset_key": dataset_key,
                 "flags_used": list(cfg.good_flags), "interpolation": "none", "is_synthetic": ds.attrs.get("is_synthetic", "false"),
                 "license": ds.attrs.get("license", ""), "Conventions": ds.attrs.get("Conventions", ""), "scats_lineage": json.dumps(lineage, sort_keys=True),
                 **{k: ds.attrs[k] for k in ("geospatial_lat_min", "geospatial_lat_max", "geospatial_lon_min", "geospatial_lon_max") if k in ds.attrs}}
        final_ds = for_zarr(out, attrs)
        final_ds.attrs["dataset_type"] = ds.attrs["dataset_type"]
        want = content_sha256(final_ds)

        if current and not force and current[8] is None and (current[2], current[4], current[5], current[6], current[7]) == (
                want, code, cfg.sha256, src["manifest_sha256"], src["review_version"]):
            return BuildResult(product_key, kind, current[1], "unchanged", "same source, code and configuration: nothing to rebuild",
                               content_sha256=current[2], manifest_sha256=current[3])
        version = _max_version(con, product_key) + 1
        final = f"{kind}/{dataset_key}/v{version}.zarr"
        try:
            res = write_zarr_safe(final_ds, s3=s3, bucket=DERIVED_BUCKET, final=final, chunker=_chunker(final_ds))
        except PublishError as exc:
            raise ProductError(f"writing the product failed at '{exc.stage}': {exc.detail} ({exc.state})") from exc
        try:
            _catalogue(con, src=src, kind=kind, product_key=product_key, version=version, res=res, lineage=lineage, code=code, cfg=cfg,
                       ds=final_ds, info=info)
        except BaseException as exc:
            left = _remove(s3, final)
            raise ProductError(f"the product was written but could not be catalogued ({type(exc).__name__}: {exc}); {left}") from exc
        return BuildResult(product_key, kind, version, "built", f"version {version}", uri=res.uri, content_sha256=res.content_sha256,
                           manifest_sha256=res.manifest_sha256)
    finally:
        con.close()


def _max_version(con, product_key: str) -> int:
    with con.cursor() as cur:
        cur.execute("SELECT COALESCE(max(version), 0) FROM derived_product WHERE product_key = %s", (product_key,))
        n = cur.fetchone()[0]
    con.rollback()
    return n


def _remove(s3: S3Store, final: str) -> str:
    try:
        n = s3.delete_prefix(DERIVED_BUCKET, final + "/")
        return f"{n} written objects were removed"
    except Exception as exc:  # noqa: BLE001
        return f"the written objects under {DERIVED_BUCKET}/{final}/ could not be removed ({type(exc).__name__})"


def _catalogue(con, *, src, kind, product_key, version, res, lineage, code, cfg, ds, info) -> None:
    """One transaction: re-check the source, supersede the old row, insert the new row, write the audit row."""
    ext = _extent(ds)
    with con:
        with con.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (product_key,))
            again = _source(cur, src["dataset_key"])                            # still published?
            if (again["store_id"], again["manifest_sha256"], again["review_version"]) != (src["store_id"], src["manifest_sha256"], src["review_version"]):
                raise ProductError("the source dataset was published again while the product was being built")
            cur.execute("SELECT COALESCE(max(version), 0) FROM derived_product WHERE product_key = %s", (product_key,))
            if cur.fetchone()[0] + 1 != version:
                raise ProductError("another build of the same product finished first")
            cur.execute("UPDATE derived_product SET superseded_at = now() WHERE product_key = %s AND superseded_at IS NULL", (product_key,))
            a = src["attributes"] or {}

            def num(k):
                try:
                    return float(a[k])
                except (KeyError, TypeError, ValueError):
                    return None
            cur.execute(
                "INSERT INTO derived_product (kind, product_key, version, dataset_id, dataset_key, dataset_type, platform_type, title, description,"
                " source_store_id, source_store_uri, source_manifest_sha256, source_content_sha256, source_review_id, source_review_version,"
                " qc_config_version, uri, zarr_format, content_sha256, manifest_sha256, n_objects, total_bytes, code_version, code_sha256,"
                " config_sha256, lineage, variables, time_start, time_end, lat_min, lat_max, lon_min, lon_max, built_by)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)",
                (kind, product_key, version, src["dataset_id"], src["dataset_key"], src["dataset_type"], a.get("platform_type"),
                 ds.attrs["title"], info["description"], src["store_id"], src["store_uri"], src["manifest_sha256"], src["content_sha256"],
                 src["review_id"], src["review_version"], src["qc_config_version"], res.uri, ZARR_FORMAT, res.content_sha256,
                 res.manifest_sha256, res.n_objects, res.total_bytes, CODE_VERSION, code, cfg.sha256, json.dumps(lineage, sort_keys=True),
                 sorted(v for v in ds.data_vars if v not in ("latitude", "longitude", "time", "platform_id", "source_record", "profile_usable",
                                                          "bin_top", "bin_bottom", "depth")),
                 ext.get("time_start"), ext.get("time_end"), num("geospatial_lat_min"), num("geospatial_lat_max"),
                 num("geospatial_lon_min"), num("geospatial_lon_max"), ACTOR))
            cur.execute("INSERT INTO audit_log (username, action, entity_type, entity_id, before_state, after_state, comment)"
                        " VALUES (%s, 'product.build', 'product', %s, NULL, %s::jsonb, %s)",
                        (ACTOR, f"{product_key} v{version}", json.dumps({"content_sha256": res.content_sha256, "uri": res.uri,
                                                                         "source_manifest_sha256": src["manifest_sha256"]}),
                         "derived product built from published data"))


def build_all(*, force: bool = False, dataset_key: str | None = None, db_url: str | None = None, s3: S3Store | None = None,
              config: ProductsConfig | None = None) -> list[BuildResult]:
    """Every product that is defined for every published dataset (or one dataset). A failure of one does not stop the rest."""
    s3 = s3 or S3Store.from_env()
    con = psycopg2.connect(db_url or _db_url(), connect_timeout=5)
    try:
        with con.cursor() as cur:
            cur.execute("SELECT d.dataset_key, d.dataset_type, COALESCE((SELECT array_agg(variable_name) FROM dataset_variable v"
                        " WHERE v.dataset_id = d.dataset_id), '{}') FROM dataset d JOIN dataset_store s ON s.dataset_id = d.dataset_id"
                        " WHERE d.status = 'published' AND (%s::text IS NULL OR d.dataset_key = %s) ORDER BY d.dataset_key", (dataset_key, dataset_key))
            todo = cur.fetchall()
        con.rollback()
    finally:
        con.close()
    out: list[BuildResult] = []
    for key, dtype, variables in todo:
        kinds = kinds_for(dtype, variables)
        if not kinds:
            out.append(BuildResult(f"-__{key}", "-", 0, "skipped", f"no product is defined for a {dtype} dataset (the standardised Zarr is served directly)"))
        for kind in kinds:
            try:
                out.append(build_product(key, kind, force=force, db_url=db_url, s3=s3, config=config))
            except ProductError as exc:
                out.append(BuildResult(f"{kind}__{key}", kind, 0, "failed", str(exc)))
            except Exception as exc:  # noqa: BLE001 - one broken dataset must not stop the others
                out.append(BuildResult(f"{kind}__{key}", kind, 0, "failed", f"unexpected {type(exc).__name__}: {str(exc)[:200]}"))
    return out
