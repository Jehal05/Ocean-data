"""Catalogue lookups, limit checks, the query log and the data requests (docs/phase4_design.md sections 4 to 8)."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
from fastapi import HTTPException

import scats_auth as auth
from scats_common.qc_config_ref import qc_config_sha256_for
from scats_store import S3Store, StoreCorrupt
from scats_store.lazy import LazyDeadline, open_lazy

from . import engine, outputs
from .db import tx
from .filters import GOOD_FLAGS, Bbox, Filter, Refused, integer, parse_bbox, parse_filter, parse_time, read_params
from .policy import HARD_MAX

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,150}$")
KINDS = ("binned_profile", "series_hourly", "series_daily", "sst_monthly_mean")
DTYPES = ("profile", "timeseries", "grid", "bathymetry")
URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")

DATASET_COLS = ("dataset_key", "title", "dataset_type", "source", "instrument", "is_synthetic", "license", "platform_type", "platform_id",
                "time_start", "time_end", "lat_min", "lat_max", "lon_min", "lon_max", "variables", "store_uri", "zarr_format",
                "content_sha256", "manifest_sha256", "n_objects", "total_bytes", "review_id", "review_version", "qc_config_version", "published_at")
PRODUCT_COLS = ("product_key", "kind", "version", "dataset_key", "dataset_type", "platform_type", "title", "description", "source_store_uri",
                "source_manifest_sha256", "source_content_sha256", "source_review_id", "source_review_version", "qc_config_version", "uri",
                "zarr_format", "content_sha256", "manifest_sha256", "n_objects", "total_bytes", "code_version", "code_sha256", "config_sha256",
                "lineage", "variables", "time_start", "time_end", "lat_min", "lat_max", "lon_min", "lon_max", "built_by", "built_at",
                "superseded_at", "stale", "stale_reason")


def _dicts(cur, cols) -> list[dict]:
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def jsonable(x):
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if hasattr(x, "isoformat"):
        return x.isoformat()
    if isinstance(x, (np.generic,)):
        return jsonable(x.item())
    if isinstance(x, float) and not np.isfinite(x):
        return None
    return x


# ------------------------------------------------------------------ limits --

def limits_for(cur, role: str) -> dict[str, int]:
    cur.execute("SELECT name, value FROM api_limit WHERE role_name = %s", (role,))
    out = {n: int(v) for n, v in cur.fetchall()}
    if set(out) != set(HARD_MAX):
        raise HTTPException(503, "the limits of this role are not configured")
    return out


def check_rate(cur, principal, limits: dict) -> None:
    cur.execute("SELECT access_recent_count(%s, 60)", (principal.user_id,))
    n = cur.fetchone()[0]
    if n >= limits["rate_per_minute"]:
        raise HTTPException(429, f"rate limit: {n} requests in the last minute (limit rate_per_minute = {limits['rate_per_minute']})",
                            headers={"Retry-After": "60"})


def write_log(cur, principal, *, action, target_type=None, target_key=None, version=None, data_version=None, qc_sha=None, flt=None,
              fmt=None, n_rows=None, n_bytes=None) -> None:
    cur.execute(
        "INSERT INTO query_log (user_id, username, role_name, action, target_type, target_key, product_version, data_version,"
        " qc_config_sha256, filter, output_format, n_rows, n_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (principal.user_id, principal.username, principal.role, action, target_type, target_key, version, data_version, qc_sha,
         json.dumps(jsonable(flt or {})), fmt, n_rows, n_bytes))


@dataclass
class Collection:
    type: str
    key: str
    version: int | None
    bucket: str
    prefix: str
    manifest_sha256: str
    content_sha256: str
    dataset_type: str
    variables: list[str]
    qc_config_version: str | None
    qc_config_sha256: str | None
    stale: bool
    stale_reason: str | None
    time_start: object
    time_end: object
    row: dict

    @property
    def is_product(self) -> bool:
        return self.type == "product"


def _split(uri: str) -> tuple[str, str]:
    m = URI_RE.match(uri)
    if not m:
        raise HTTPException(500, "the catalogue holds an address that cannot be read")
    return m.group(1), m.group(2)


def find_collection(cur, typ: str, key: str, version_text: str | None = None) -> Collection:
    if not KEY_RE.match(key):
        raise HTTPException(404, "not found")
    if typ == "dataset":
        if version_text is not None:
            raise Refused(422, "datasets have no version parameter")
        cur.execute(f"SELECT {', '.join(DATASET_COLS)} FROM catalogue_dataset WHERE dataset_key = %s", (key,))
        rows = _dicts(cur, DATASET_COLS)
        if not rows:
            raise HTTPException(404, "not found")
        r = rows[0]
        b, p = _split(r["store_uri"])
        return Collection("dataset", key, None, b, p, r["manifest_sha256"], r["content_sha256"], r["dataset_type"], list(r["variables"]),
                          r["qc_config_version"], qc_config_sha256_for(r["qc_config_version"]), False, None, r["time_start"], r["time_end"], r)
    if version_text is None:
        cur.execute(f"SELECT {', '.join(PRODUCT_COLS)} FROM catalogue_product WHERE product_key = %s AND superseded_at IS NULL", (key,))
    else:
        v = integer(version_text, "version", 1, 10**6)
        cur.execute(f"SELECT {', '.join(PRODUCT_COLS)} FROM catalogue_product WHERE product_key = %s AND version = %s", (key, v))
    rows = _dicts(cur, PRODUCT_COLS)
    if not rows:
        raise HTTPException(404, "not found")
    r = rows[0]
    b, p = _split(r["uri"])
    src = (r["lineage"] or {}).get("source", {})
    return Collection("product", key, r["version"], b, p, r["manifest_sha256"], r["content_sha256"], r["dataset_type"], list(r["variables"]),
                      r["qc_config_version"], src.get("qc_config_sha256"), bool(r["stale"]), r["stale_reason"], r["time_start"], r["time_end"], r)


def info(col: Collection) -> dict:
    """The facts a user needs to cite what they got."""
    return {"collection": {"type": col.type, "key": col.key}, "data_version": col.content_sha256, "product_version": col.version,
            "qc_config_version": col.qc_config_version, "qc_config_sha256": col.qc_config_sha256,
            "flags_used": list(GOOD_FLAGS) if col.is_product else "set by the quality parameter (good = 1, 2; all = every flag, shown in *_qc)",
            "stale": col.stale, "stale_reason": col.stale_reason}


# -------------------------------------------------------------- discovery --

def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def discover(principal, typ: str, query_params) -> dict:
    raw = {k: v for k, v in query_params.items()}
    allowed = {"q", "platform_type", "dataset_type", "variable", "kind", "time_from", "time_to", "bbox", "include_stale", "page", "page_size"}
    for k in query_params.keys():
        if k not in allowed:
            raise Refused(422, f"unknown parameter {k[:40]!r}")
        if len(query_params.getlist(k)) != 1 or len(raw[k]) > 200 or any(ord(c) < 32 for c in raw[k]):
            raise Refused(422, f"parameter {k} is invalid")
    where, args = [], []
    view = "catalogue_dataset" if typ == "dataset" else "catalogue_product"
    keycol = "dataset_key" if typ == "dataset" else "product_key"
    if "q" in raw:
        where.append(f"(title ILIKE %s ESCAPE '\\' OR {keycol} ILIKE %s ESCAPE '\\' OR COALESCE(platform_type,'') ILIKE %s ESCAPE '\\')")
        args += [_like(raw["q"])] * 3
    if "platform_type" in raw:
        if not re.match(r"^[A-Za-z0-9_]{1,60}$", raw["platform_type"]):
            raise Refused(422, "platform_type must be letters, digits or _")
        where.append("platform_type = %s")
        args.append(raw["platform_type"])
    if "dataset_type" in raw:
        if raw["dataset_type"] not in DTYPES:
            raise Refused(422, f"dataset_type must be one of {', '.join(DTYPES)}")
        where.append("dataset_type = %s")
        args.append(raw["dataset_type"])
    if "variable" in raw:
        if not re.match(r"^[A-Za-z0-9_]{1,80}$", raw["variable"]):
            raise Refused(422, "variable must be letters, digits or _")
        where.append("%s = ANY(variables)")
        args.append(raw["variable"])
    if "kind" in raw:
        if typ != "product" or raw["kind"] not in KINDS:
            raise Refused(422, f"kind applies to products and must be one of {', '.join(KINDS)}")
        where.append("kind = %s")
        args.append(raw["kind"])
    if "time_from" in raw or "time_to" in raw:
        tf = parse_time(raw["time_from"], "time_from", end=False) if "time_from" in raw else None
        tt = parse_time(raw["time_to"], "time_to", end=True) if "time_to" in raw else None
        if tf is not None and tt is not None and tf > tt:
            raise Refused(422, "time_from must not be later than time_to")
        where.append("time_start IS NOT NULL")
        if tf is not None:
            where.append("time_end >= %s")
            args.append(str(tf)[:19] + "+00")
        if tt is not None:
            where.append("time_start <= %s")
            args.append(str(tt)[:19] + "+00")
    if "bbox" in raw:
        b: Bbox = parse_bbox(raw["bbox"])
        where.append("lat_max >= %s AND lat_min <= %s")
        args += [b.south, b.north]
        if not b.full_lon:
            # Longitude ranges live on a circle: the box may cross 180 (west > east) and so may a footprint (lon_min > lon_max).
            # Cut the box into plain pieces; a plain footprint meets a piece when it overlaps it, a footprint that crosses 180
            # is the two plain pieces [lon_min,180] and [-180,lon_max], which meet [a,b] when lon_min <= b or lon_max >= a.
            pieces = [(b.west, b.east)] if b.west <= b.east else [(b.west, 180.0), (-180.0, b.east)]
            tests = []
            for a, z in pieces:
                tests.append("((lon_min <= lon_max AND lon_max >= %s AND lon_min <= %s) OR (lon_min > lon_max AND (lon_min <= %s OR lon_max >= %s)))")
                args += [a, z, z, a]
            where.append("(" + " OR ".join(tests) + ")")
    if typ == "product":
        where.append("superseded_at IS NULL")
        if raw.get("include_stale") != "true":
            where.append("NOT stale")
    cols = DATASET_COLS if typ == "dataset" else PRODUCT_COLS
    brief = [c for c in cols if c not in ("lineage", "store_uri", "uri", "source_store_uri")]
    with tx() as cur:
        limits = limits_for(cur, principal.role)
        check_rate(cur, principal, limits)
        size = integer(raw["page_size"], "page_size", 1, limits["page_size_max"]) if "page_size" in raw else min(100, limits["page_size_max"])
        page = integer(raw["page"], "page", 1, 10**6) if "page" in raw else 1
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT count(*) FROM {view}{sql_where}", args)
        total = cur.fetchone()[0]
        cur.execute(f"SELECT {', '.join(brief)} FROM {view}{sql_where} ORDER BY {keycol}, 1 LIMIT %s OFFSET %s", args + [size, (page - 1) * size])
        items = _dicts(cur, brief)
        write_log(cur, principal, action="discover", target_type=typ, flt={k: v for k, v in raw.items()}, n_rows=len(items))
    more = page * size < total
    return jsonable({"type": typ, "total": total, "page": page, "page_size": size, "truncated": more, "next_page": page + 1 if more else None,
                     "items": items})


def metadata(principal, typ: str, key: str, version_text: str | None) -> dict:
    with tx() as cur:
        limits = limits_for(cur, principal.role)
        check_rate(cur, principal, limits)
        col = find_collection(cur, typ, key, version_text)
        out = {k: v for k, v in col.row.items() if k not in ("store_uri", "uri", "source_store_uri")}
        out.update(info(col))
        if typ == "product":
            cur.execute("SELECT version, built_at, superseded_at, stale, content_sha256 FROM catalogue_product WHERE product_key = %s ORDER BY version", (key,))
            out["versions"] = _dicts(cur, ("version", "built_at", "superseded_at", "stale", "content_sha256"))
        write_log(cur, principal, action="metadata", target_type=typ, target_key=key, version=col.version, data_version=col.content_sha256,
                  qc_sha=col.qc_config_sha256)
    return jsonable(out)


def zarr_listing(principal, typ: str, key: str, version_text: str | None) -> dict:
    with tx() as cur:
        limits = limits_for(cur, principal.role)
        check_rate(cur, principal, limits)
        col = find_collection(cur, typ, key, version_text)
    s3 = S3Store.from_env()
    try:
        from scats_store.checksum import manifest_sha256 as msha
        manifest = json.loads(s3.get_bytes(col.bucket, f"{col.prefix}/scats_manifest.json"))
        objs = {k: (v["sha256"], v["size"]) for k, v in manifest["objects"].items()}
        if msha(objs) != col.manifest_sha256 or manifest["manifest_sha256"] != col.manifest_sha256:
            raise StoreCorrupt("the manifest does not match the catalogue")
    except StoreCorrupt as exc:
        raise HTTPException(502, str(exc))
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "the store could not be read")
    if len(objs) > 10000:
        raise Refused(413, "the store has more than 10000 objects; the listing is not available")
    out = {**info(col), "uri": col.row.get("store_uri") or col.row.get("uri"), "zarr_format": manifest["zarr_format"],
           "manifest_sha256": col.manifest_sha256, "n_objects": len(objs),
           "objects": [{"path": k, "size": v[1], "sha256": v[0]} for k, v in sorted(objs.items())]}
    _log_checked(principal, typ, key, col, action="zarr_listing", n_rows=len(objs))
    return jsonable(out)


def _log_checked(principal, typ, key, col: Collection, *, action, flt=None, fmt=None, n_rows=None, n_bytes=None, allow_stale=False) -> None:
    """Re-read the catalogue row and write the log row in ONE transaction. If either fails nothing is returned."""
    try:
        with tx() as cur:
            again = find_collection(cur, typ, key, None if typ == "dataset" else str(col.version))
            if again.manifest_sha256 != col.manifest_sha256 or again.content_sha256 != col.content_sha256:
                raise Refused(409, "the collection changed while the request was being served; ask again")
            if again.stale and not allow_stale and action in ("subset", "download"):
                raise Refused(409, f"the product became stale: {again.stale_reason}")
            write_log(cur, principal, action=action, target_type=typ, target_key=key, version=col.version, data_version=col.content_sha256,
                      qc_sha=col.qc_config_sha256, flt=flt, fmt=fmt, n_rows=n_rows, n_bytes=n_bytes)
    except (HTTPException, Exception) as exc:  # noqa: BLE001
        if isinstance(exc, Refused):
            raise
        raise HTTPException(503, "the request could not be logged, so nothing was returned") from exc


# -------------------------------------------------------- subset / download --

def _span_check(flt: Filter, col: Collection, limits: dict) -> None:
    mx = limits["max_time_span_days"]
    if flt.bbox is not None and flt.bbox.area > limits["max_bbox_area_deg2"]:
        raise Refused(413, f"the bounding box covers {flt.bbox.area:.0f} square degrees (limit max_bbox_area_deg2 = {limits['max_bbox_area_deg2']})")
    if col.dataset_type not in ("profile", "timeseries", "grid"):
        return
    lo = np.datetime64(col.time_start.replace(tzinfo=None), "ns") if col.time_start else None
    hi = np.datetime64(col.time_end.replace(tzinfo=None), "ns") if col.time_end else None
    a = flt.time_from if flt.time_from is not None else lo
    b = flt.time_to if flt.time_to is not None else hi
    if a is None or b is None:
        return
    days = (b - a) / np.timedelta64(1, "D")
    if days > mx:
        raise Refused(413, f"the time range is {days:.0f} days (limit max_time_span_days = {mx}); give time_from and time_to")


def data_request(principal, typ: str, key: str, query_params, action: str) -> tuple[Collection, Filter, dict, pd.DataFrame, engine.Plan, dict]:
    raw = read_params(query_params)
    started = time.monotonic()
    with tx() as cur:
        limits = limits_for(cur, principal.role)
        check_rate(cur, principal, limits)
        col = find_collection(cur, typ, key, raw.get("version"))
    allow_stale = raw.get("allow_stale") == "true"
    if raw.get("allow_stale", "true") not in ("true", "false"):
        raise Refused(422, "allow_stale must be true or false")
    if col.stale and not allow_stale:
        raise Refused(409, f"this product is stale: {col.stale_reason}. Rebuild it, or pass allow_stale=true to read it anyway")
    ax = engine.axes_for(col.dataset_type)
    flt = parse_filter(raw, variables=col.variables, has_time=ax["time"], has_depth=ax["depth"], has_platform=ax["platform"], is_product=col.is_product)
    _span_check(flt, col, limits)
    size = integer(raw["page_size"], "page_size", 1, limits["page_size_max"]) if "page_size" in raw else min(100, limits["page_size_max"])
    page = integer(raw["page"], "page", 1, 10**6) if "page" in raw else 1
    s3 = S3Store.from_env()
    try:
        ds, store, _m = open_lazy(s3, col.bucket, col.prefix, expected_manifest_sha256=col.manifest_sha256)
        store.with_deadline(limits["request_timeout_s"])
        plan = engine.make_plan(ds, flt, is_product=col.is_product)
        cap = limits["max_rows_subset"] if action == "subset" else limits["max_rows_download"]
        if plan.n_rows > cap:
            what = "max_rows_subset" if action == "subset" else "max_rows_download"
            hint = " (narrow the filters, or use /download)" if action == "subset" else " (narrow the filters)"
            raise Refused(413, f"the selection has {plan.n_rows} rows (limit {what} = {cap}){hint}")
        if action == "subset":
            df = engine.table(ds, plan, (page - 1) * size, page * size)
            paging = {"page": page, "page_size": size, "truncated": page * size < plan.n_rows, "next_page": page + 1 if page * size < plan.n_rows else None}
        else:
            df = engine.table(ds, plan, 0, plan.n_rows)
            paging = {}
        if time.monotonic() - started > limits["request_timeout_s"]:
            raise LazyDeadline("the request ran out of time")
        units = {n: ds[n].attrs.get("units", "") for n in ds.variables}
    except LazyDeadline:
        raise HTTPException(504, f"the request used more than request_timeout_s = {limits['request_timeout_s']} seconds")
    except StoreCorrupt as exc:
        raise HTTPException(502, f"the stored data failed its check: {exc}")
    return col, flt, {"limits": limits, "paging": paging, "units": units, "allow_stale": allow_stale, "raw": raw}, df, plan, {}


def subset(principal, typ: str, key: str, query_params) -> dict:
    col, flt, ctx, df, plan, _ = data_request(principal, typ, key, query_params, "subset")
    body = {**info(col), "filter": flt.echo(), "n_rows_selected": plan.n_rows, **ctx["paging"], "columns": list(df.columns),
            "units": {c: ctx["units"].get(c, "") for c in df.columns}, "rows": outputs.json_rows(df)}
    _log_checked(principal, typ, key, col, action="subset", flt=flt.echo(), fmt="json", n_rows=len(df), n_bytes=None, allow_stale=ctx["allow_stale"])
    return jsonable(body)


def download(principal, typ: str, key: str, query_params) -> tuple[bytes, str, str, dict]:
    fmt = query_params.get("format", "csv")
    if fmt not in ("csv", "netcdf"):
        raise Refused(422, "format must be csv or netcdf")
    col, flt, ctx, df, plan, _ = data_request(principal, typ, key, query_params, "download")
    if fmt == "csv":
        data, mime, ext = outputs.csv_bytes(df), "text/csv; charset=utf-8", "csv"
    else:
        attrs = {"title": f"{typ} {key}", "data_version": col.content_sha256, "product_version": col.version if col.version else 0,
                 "qc_config_version": col.qc_config_version or "", "qc_config_sha256": col.qc_config_sha256 or "unknown",
                 "flags_used": "1,2" if (col.is_product or flt.quality == "good") else "all (see *_qc columns)", "filter": json.dumps(flt.echo()),
                 "stale": str(col.stale).lower()}
        data, mime, ext = outputs.netcdf_bytes(df, attrs, ctx["units"]), "application/x-netcdf", "nc"
    if len(data) > ctx["limits"]["max_bytes_download"]:
        raise Refused(413, f"the file would be {len(data)} bytes (limit max_bytes_download = {ctx['limits']['max_bytes_download']})")
    _log_checked(principal, typ, key, col, action="download", flt=flt.echo(), fmt=fmt, n_rows=len(df), n_bytes=len(data), allow_stale=ctx["allow_stale"])
    headers = {"X-SCATS-Data-Version": col.content_sha256, "X-SCATS-Product-Version": str(col.version or ""),
               "X-SCATS-QC-Config-SHA256": col.qc_config_sha256 or "unknown", "X-SCATS-QC-Config-Version": col.qc_config_version or "unknown",
               "X-SCATS-Flags-Used": "1,2" if (col.is_product or flt.quality == "good") else "all", "X-SCATS-Stale": str(col.stale).lower(),
               "X-SCATS-Rows": str(len(df))}
    return data, mime, outputs.safe_filename(key, col.version, ext), headers


# ------------------------------------------------------------------ limits --

def show_limits(principal) -> dict:
    with tx() as cur:
        lim = limits_for(cur, principal.role)
    from .policy import LIMIT_TEXT
    return {"role": principal.role, "limits": [{"name": k, "value": v, "hard_max": HARD_MAX[k], "meaning": LIMIT_TEXT[k]} for k, v in sorted(lim.items())]}


def set_limit(token: str, role: str, name: str, value: int) -> dict:
    if name not in HARD_MAX:
        raise HTTPException(404, "no such limit")
    if role not in ("viewer", "reviewer", "admin"):
        raise HTTPException(404, "no such role")
    if value < 1 or value > HARD_MAX[name]:
        raise Refused(422, f"{name} must be between 1 and {HARD_MAX[name]}")
    try:
        with tx() as cur:
            cur.execute("SELECT access_set_limit(%s, %s, %s, %s)", (auth.hash_token(token), role, name, value))
            old = cur.fetchone()[0]
    except HTTPException:
        raise
    return {"role": role, "name": name, "old": old, "new": value}
