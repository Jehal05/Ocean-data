"""Review logic. Every function that changes anything does it, with its audit row, in ONE transaction."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import numpy as np
import psycopg2.extras as pgx
from fastapi import HTTPException

from scats_common import MODEL
from scats_common.flags import PERSISTED
from scats_ingest.limits import LIMITS as QC_LIMITS
from scats_store import PublishError, S3Store, apply_human_flags, for_zarr, publish_zarr, verify_store_consistency

from . import stagingcache
from .db import tx
from .policy import CONFIRMABLE, HUMAN_FLAGS, MAX_FLAG_BATCH, PAGE_MAX, PAGE_SIZE, REVIEW_LIMITS, STATUSES, settings

AUX = ("position", "time")


# ----------------------------------------------------------------- helpers --

def _dumps(x) -> str:
    return json.dumps(x, default=str, allow_nan=False)


def clean(x):
    """JSON-safe numbers: NaN and infinity become null."""
    if isinstance(x, np.ndarray):
        x = x.tolist()
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def get_dataset(cur, key: str, lock: str | None = None) -> dict:
    cur.execute("SELECT dataset_id, dataset_key, status, dataset_type, attributes FROM dataset WHERE dataset_key = %s"
                + {"share": " FOR SHARE", "update": " FOR UPDATE"}.get(lock, ""), (key,))
    r = cur.fetchone()
    if r is None:
        raise HTTPException(404, f"dataset '{key}' not found")
    return {"id": r[0], "key": r[1], "status": r[2], "type": r[3], "attributes": r[4]}


def audit(cur, principal, action: str, key: str, before, after, comment: str | None):
    cur.execute(
        "INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, before_state, after_state, comment)"
        " VALUES (%s, %s, %s, 'dataset', %s, %s::jsonb, %s::jsonb, %s)",
        (principal.user_id, principal.username, action, key, None if before is None else _dumps(before),
         None if after is None else _dumps(after), comment))


def need_comment(comment: str | None) -> str:
    c = (comment or "").strip()
    if not c:
        raise HTTPException(422, "a comment (the reason) is required")
    if len(c) > 2000:
        raise HTTPException(422, "the comment is longer than 2000 characters")
    return c


def page_args(page: int, size: int | None) -> tuple[int, int]:
    size = PAGE_SIZE if size is None else size
    if page < 1 or size < 1 or size > PAGE_MAX:
        raise HTTPException(422, f"page must be >= 1 and page_size between 1 and {PAGE_MAX}")
    return size, (page - 1) * size


# ---------------------------------------------------------------- reading --

def list_datasets(status: str | None, page: int, size: int | None):
    if status is not None and status not in STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(STATUSES)}")
    size, offset = page_args(page, size)
    with tx() as cur:
        cur.execute(
            "SELECT c.dataset_key, d.title, d.dataset_type, d.status, d.attributes->>'is_synthetic', c.n_flag3, c.n_flag4,"
            " c.n_flag9, c.n_human, c.n_unreviewed_flag4, (SELECT r.decision FROM review r WHERE r.dataset_id = d.dataset_id"
            " ORDER BY r.review_id DESC LIMIT 1), count(*) OVER () FROM dataset_review_counts c JOIN dataset d USING (dataset_id)"
            " WHERE (%s::text IS NULL OR d.status = %s) ORDER BY d.dataset_key LIMIT %s OFFSET %s",
            (status, status, size, offset))
        rows = cur.fetchall()
    return {"page": page, "page_size": size, "total": rows[0][11] if rows else 0,
            "datasets": [{"dataset_key": r[0], "title": r[1], "dataset_type": r[2], "status": r[3],
                          "synthetic_status": r[4] or "unknown", "flags_probably_bad": r[5], "flags_bad": r[6],
                          "flags_missing": r[7], "human_decisions": r[8], "unreviewed_flag4": r[9],
                          "last_decision": r[10]} for r in rows]}


def dataset_detail(key: str) -> dict:
    with tx() as cur:
        d = get_dataset(cur, key)
        did, a = d["id"], d["attributes"] or {}
        cur.execute("SELECT count(*), COALESCE(max(overall_flag), 0) FROM observation WHERE dataset_id = %s", (did,))
        n_obs, worst = cur.fetchone()
        base = ("FROM qc_flag_current q JOIN observation o USING (observation_id) WHERE o.dataset_id = %s"
                " AND (q.flag IN (2,3,4,5,8,9) OR q.set_by_user_id IS NOT NULL)")
        cur.execute(f"SELECT q.flag, count(*) {base} GROUP BY 1 ORDER BY 1", (did,))
        by_flag = {int(f): n for f, n in cur.fetchall()}
        cur.execute(f"SELECT q.test_id, count(*) {base} GROUP BY 1 ORDER BY 2 DESC", (did,))
        by_test = dict(cur.fetchall())
        cur.execute(f"SELECT q.variable_name, count(*) {base} GROUP BY 1 ORDER BY 2 DESC", (did,))
        by_var = dict(cur.fetchall())
        cur.execute("SELECT after_state->>'config_version' FROM audit_log WHERE action = 'qc.run' AND entity_id = %s"
                    " ORDER BY audit_id DESC LIMIT 1", (key,))
        cfg = cur.fetchone()
        cur.execute("SELECT n_unreviewed_flag4, n_human FROM dataset_review_counts WHERE dataset_id = %s", (did,))
        unrev, human = cur.fetchone()
        cur.execute("SELECT r.review_id, u.username, r.decision, r.comment, r.decided_at, r.review_version,"
                    " r.unreviewed_flag4, r.accepted_unreviewed_flag4 FROM review r LEFT JOIN app_user_public u"
                    " ON u.user_id = r.reviewer_id WHERE r.dataset_id = %s ORDER BY r.review_id", (did,))
        reviews = [dict(zip(("review_id", "reviewer", "decision", "comment", "decided_at", "review_version",
                             "unreviewed_flag4", "accepted_unreviewed_flag4"), r)) for r in cur.fetchall()]
        cur.execute("SELECT store_uri, zarr_format, content_sha256, manifest_sha256, n_objects, total_bytes, review_version,"
                    " published_at, staging_superseded_at FROM dataset_store WHERE dataset_id = %s", (did,))
        st = cur.fetchone()
        cur.execute("SELECT title, source, time_start, time_end FROM dataset WHERE dataset_id = %s", (did,))
        title, source, t0, t1 = cur.fetchone()
    return {"dataset_key": key, "title": title, "source": source, "dataset_type": d["type"], "status": d["status"],
            "time_start": t0, "time_end": t1,
            "synthetic_status": a.get("is_synthetic") or "unknown",
            "units_assumed_from_mapping": a.get("units_assumed_from_mapping") == "true",
            "qc_reference_time": a.get("qc_reference_time"), "qc_config_version": cfg[0] if cfg else None,
            "observations": n_obs, "worst_overall_flag_automatic": int(worst),
            "current_flags_by_value": by_flag, "current_flags_by_test": by_test, "current_flags_by_variable": by_var,
            "human_decisions": human, "unreviewed_flag4": unrev, "reviews": reviews,
            "store": None if st is None else dict(zip(
                ("store_uri", "zarr_format", "content_sha256", "manifest_sha256", "n_objects", "total_bytes",
                 "review_version", "published_at", "staging_superseded_at"), st)),
            "limits": QC_LIMITS + REVIEW_LIMITS}


_FLAG_COLS = ("qc_flag_id", "observation_id", "source_record", "platform_id", "obs_time", "variable", "level_index",
              "flag", "test_id", "set_by", "comment", "detail", "set_at", "automatic_flag", "automatic_test")


def _flag_query(where: str) -> str:
    return ("SELECT q.qc_flag_id, o.observation_id, o.source_record, o.platform_id, o.obs_time, q.variable_name,"
            " q.level_index, q.flag, q.test_id, u.username, q.comment, q.detail, q.set_at, a.flag, a.test_id, count(*) OVER ()"
            " FROM qc_flag_current q JOIN observation o USING (observation_id)"
            " LEFT JOIN app_user_public u ON u.user_id = q.set_by_user_id"
            " LEFT JOIN qc_flag_auto_current a ON a.observation_id = q.observation_id AND a.variable_name = q.variable_name"
            " AND a.level_index = q.level_index"
            f" WHERE o.dataset_id = %s AND (q.flag IN (2,3,4,5,8,9) OR q.set_by_user_id IS NOT NULL) {where}"
            " ORDER BY o.source_record, q.variable_name, q.level_index")


def _history(cur, obs_ids: list[int]) -> dict:
    if not obs_ids:
        return {}
    cur.execute("SELECT q.observation_id, q.variable_name, q.level_index, q.qc_flag_id, q.flag, q.test_id, u.username,"
                " q.comment, q.set_at FROM qc_flag q LEFT JOIN app_user_public u ON u.user_id = q.set_by_user_id"
                " WHERE q.observation_id = ANY(%s) ORDER BY q.qc_flag_id", (obs_ids,))
    out: dict = {}
    for o, v, lvl, fid, f, t, who, c, at in cur.fetchall():
        out.setdefault((o, v, lvl), []).append({"qc_flag_id": fid, "flag": f, "test_id": t, "set_by": who,
                                                "comment": c, "set_at": at})
    return out


def list_flags(key: str, variable, flag, test, reviewed, page: int, size: int | None):
    size, offset = page_args(page, size)
    where, params = "", []
    if variable is not None:
        where += " AND q.variable_name = %s"
        params.append(variable)
    if flag is not None:
        where += " AND q.flag = %s"
        params.append(flag)
    if test is not None:
        where += " AND q.test_id = %s"
        params.append(test)
    if reviewed is not None:
        where += " AND (q.set_by_user_id IS NOT NULL) = %s"
        params.append(reviewed)
    with tx() as cur:
        d = get_dataset(cur, key)
        cur.execute(_flag_query(where) + " LIMIT %s OFFSET %s", (d["id"], *params, size, offset))
        rows = cur.fetchall()
        hist = _history(cur, sorted({r[1] for r in rows}))
    items = []
    for r in rows:
        item = dict(zip(_FLAG_COLS, r[:-1]))
        item["history"] = hist.get((r[1], r[5], r[6]), [])
        items.append(item)
    return clean({"dataset_key": key, "page": page, "page_size": size, "total": rows[0][-1] if rows else 0,
                  "flags": items})


def cell_view(key: str, observation_id: int, variable, level_index, window: int):
    if not 1 <= window <= 25:
        raise HTTPException(422, "window must be between 1 and 25")
    s = settings()
    with tx() as cur:
        d = get_dataset(cur, key)
        if d["type"] in ("grid", "bathymetry"):
            raise HTTPException(422, "cell views are not available for gridded datasets")
        cur.execute("SELECT source_record, platform_id, obs_time, n_levels FROM observation"
                    " WHERE dataset_id = %s AND observation_id = %s", (d["id"], observation_id))
        o = cur.fetchone()
        if o is None:
            raise HTTPException(404, "observation not found in this dataset")
        rec, platform, obs_time, n_lev = o
        cur.execute(_flag_query(" AND o.observation_id = %s"), (d["id"], observation_id))
        rows = cur.fetchall()
        hist = _history(cur, [observation_id])
        ds = stagingcache.load(s["staging_dir"], key)
        kind = d["type"]
        names = [variable] if variable and variable not in AUX else sorted({r[5] for r in rows if r[5] not in AUX}) or \
            [v for v in MODEL.variables if v in ds.data_vars][:1]
        series = []
        for name in names:
            if name not in ds.data_vars or f"{name}_qc" not in ds:
                raise HTTPException(422, f"unknown variable '{name}'")
            units = MODEL.variables[name].units if name in MODEL.variables else None
            if kind == "profile":
                n = int(ds["n_levels"].values[rec])
                centre = level_index if level_index is not None and level_index >= 0 else 0
                lo, hi = max(0, centre - window), min(n, centre + window + 1)
                idx = list(range(lo, hi))
                values = ds[name].values[rec, lo:hi]
                staged = ds[f"{name}_qc"].values[rec, lo:hi]
                axis = {"level_index": idx, "depth_m": ds["depth"].values[rec, lo:hi]}
                cur.execute("SELECT q.level_index, q.flag FROM qc_flag_current q WHERE q.observation_id = %s"
                            " AND q.variable_name = %s AND q.level_index BETWEEN %s AND %s", (observation_id, name, lo, hi))
                over = dict(cur.fetchall())
                eff = [int(over.get(i, staged[j])) for j, i in enumerate(idx)]
            else:
                lo, hi = max(0, rec - window), min(ds.sizes["record"], rec + window + 1)
                idx = list(range(lo, hi))
                values = ds[name].values[lo:hi]
                staged = ds[f"{name}_qc"].values[lo:hi]
                axis = {"record": idx, "time": [str(t)[:19] for t in ds["time"].values[lo:hi]]}
                cur.execute("SELECT o.source_record, q.flag FROM qc_flag_current q JOIN observation o USING (observation_id)"
                            " WHERE o.dataset_id = %s AND q.variable_name = %s AND q.level_index = -1"
                            " AND o.source_record BETWEEN %s AND %s", (d["id"], name, lo, hi - 1))
                over = dict(cur.fetchall())
                eff = [int(over.get(i, staged[j])) for j, i in enumerate(idx)]
            series.append({"variable": name, "units": units, **axis, "value": values.tolist(),
                           "staging_flag": [int(x) for x in staged], "effective_flag": eff, "centre_record": rec})
    items = []
    for r in rows:
        item = dict(zip(_FLAG_COLS, r[:-1]))
        item["history"] = hist.get((r[1], r[5], r[6]), [])
        items.append(item)
    return clean({"dataset_key": key, "observation": {"observation_id": observation_id, "source_record": rec,
                                                     "platform_id": platform, "obs_time": obs_time, "n_levels": n_lev,
                                                     "latitude": float(ds["latitude"].values[rec]),
                                                     "longitude": float(ds["longitude"].values[rec])},
                  "flags": items, "series": series})


# --------------------------------------------------------------- changing --

def _validate_cells(cur, d: dict, cells, mode: str, new_flag: int | None):
    """Returns the list of (observation_id, variable, level, current_flag, current_test, record) or raises 422."""
    if len(cells) > MAX_FLAG_BATCH:
        raise HTTPException(422, f"at most {MAX_FLAG_BATCH} cells per request (got {len(cells)})")
    seen = [(c.observation_id, c.variable, c.level_index) for c in cells]
    if len(set(seen)) != len(seen):
        raise HTTPException(422, "the same cell appears twice in the request")
    ids = sorted({c.observation_id for c in cells})
    cur.execute("SELECT observation_id, source_record FROM observation WHERE dataset_id = %s AND observation_id = ANY(%s)",
                (d["id"], ids))
    rec_of = dict(cur.fetchall())
    cur.execute("SELECT observation_id, variable_name, level_index, flag, test_id, set_by_user_id FROM qc_flag_current"
                " WHERE observation_id = ANY(%s)", (ids,))
    eff = {(o, v, lvl): (f, t, u) for o, v, lvl, f, t, u in cur.fetchall()}
    kind = d["type"]
    ds = None if kind in ("grid", "bathymetry") else stagingcache.load(settings()["staging_dir"], d["key"])
    if mode == "change" and kind in ("grid", "bathymetry"):
        raise HTTPException(422, "cell-level changes are not available for gridded datasets (they can be confirmed)")
    problems, out = [], []
    for n, c in enumerate(cells):
        where = f"cell {n} (observation {c.observation_id}, {c.variable}, level {c.level_index})"
        if c.observation_id not in rec_of:
            problems.append(f"{where}: observation is not in this dataset")
            continue
        rec = rec_of[c.observation_id]
        row = eff.get((c.observation_id, c.variable, c.level_index))
        if ds is not None:
            name = f"{c.variable}_qc"
            if name not in ds:
                problems.append(f"{where}: unknown variable")
                continue
            profile_cell = kind == "profile" and c.variable not in AUX
            if profile_cell:
                if not 0 <= c.level_index < int(ds["n_levels"].values[rec]):
                    problems.append(f"{where}: level is outside this profile")
                    continue
                staged = int(ds[name].values[rec, c.level_index])
            else:
                if c.level_index != -1:
                    problems.append(f"{where}: level_index must be -1 for this variable")
                    continue
                staged = int(ds[name].values[rec])
            current = row[0] if row else staged
        else:
            if row is None:
                problems.append(f"{where}: nothing to confirm here")
                continue
            current = row[0]
        if mode == "change":
            if current == 9:
                problems.append(f"{where}: the value is missing (flag 9); it cannot be changed")
            elif current == new_flag:
                problems.append(f"{where}: already has flag {current}")
        else:
            if row is None or row[2] is not None:
                problems.append(f"{where}: " + ("nothing to confirm here" if row is None else "a person has already decided this cell"))
            elif current not in CONFIRMABLE:
                problems.append(f"{where}: flag {current} cannot be confirmed")
        out.append((c.observation_id, c.variable, c.level_index, current, row[1] if row else None, rec))
    if problems:
        raise HTTPException(422, {"message": "no cell was changed", "problems": problems[:50], "n_problems": len(problems)})
    return out


def _write_human_flags(cur, principal, d, mode, checked, new_flag, comment):
    comment = need_comment(comment)
    rows = []
    for obs, var, lvl, current, test, _rec in checked:
        flag = current if mode == "confirm" else new_flag
        detail = {"action": mode, "previous_flag": current, "previous_test": test}
        rows.append((obs, var, lvl, flag, "expert_review", principal.user_id, comment, _dumps(detail)))
    pgx.execute_values(cur, "INSERT INTO qc_flag (observation_id, variable_name, level_index, flag, test_id, set_by_user_id,"
                            " comment, detail) VALUES %s", rows, template="(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)")
    changes = [{"observation_id": r[0], "variable": r[1], "level_index": r[2], "from": r[3] if mode == "change" else None,
                "to": r[3] if mode == "confirm" else new_flag} for r in checked]
    audit(cur, principal, "flag.change" if mode == "change" else "flag.confirm", d["key"], None,
          {"flag": new_flag, "n_cells": len(rows), "cells": changes}, comment)
    return changes


def change_flags(key: str, principal, cells, flag: int, comment: str, mode: str = "change"):
    if mode == "change" and flag not in HUMAN_FLAGS:
        raise HTTPException(422, f"a person may set flags {HUMAN_FLAGS} only")
    with tx() as cur:
        d = get_dataset(cur, key, lock="share")          # blocks an approval that is waiting for the exclusive lock
        if d["status"] != "in_review":
            raise HTTPException(409, f"flags can be changed only while the dataset is in_review (status is {d['status']})")
        checked = _validate_cells(cur, d, cells, mode, flag if mode == "change" else None)
        changes = _write_human_flags(cur, principal, d, mode, checked, flag if mode == "change" else None, comment)
    return {"dataset_key": key, "action": mode, "n_cells": len(changes), "cells": changes}


# --------------------------------------------------------------- decisions --

def start_review(key: str, principal):
    with tx() as cur:
        d = get_dataset(cur, key, lock="update")
        if d["status"] != "qc_done":
            raise HTTPException(409, f"review can start only from qc_done (status is {d['status']})")
        cur.execute("UPDATE dataset SET status = 'in_review', updated_at = now() WHERE dataset_id = %s", (d["id"],))
        audit(cur, principal, "review.start", key, {"status": "qc_done"}, {"status": "in_review"}, "review started")
    return {"dataset_key": key, "status": "in_review"}


def decide(key: str, principal, decision: str, comment: str, accept_unreviewed_flag4: bool):
    comment = need_comment(comment)
    s = settings()
    with tx() as cur:
        d = get_dataset(cur, key, lock="update")          # waits for flag changes in progress, blocks new ones
        if d["status"] != "in_review":
            raise HTTPException(409, f"a decision needs status in_review (status is {d['status']})")
        unreviewed, version, accepted = 0, None, False
        if decision == "approved":
            if s["four_eyes"]:
                cur.execute("SELECT 1 FROM audit_log WHERE action = 'dataset.ingest' AND entity_type = 'dataset'"
                            " AND entity_id = %s AND username = %s LIMIT 1", (key, principal.username))
                if cur.fetchone():
                    raise HTTPException(403, "four-eyes rule: you ingested this dataset, so someone else must approve it")
            cur.execute("SELECT count(*) FROM qc_flag_current q JOIN observation o USING (observation_id)"
                        " WHERE o.dataset_id = %s AND q.flag = 4 AND q.set_by_user_id IS NULL", (d["id"],))
            unreviewed = cur.fetchone()[0]
            if unreviewed and not accept_unreviewed_flag4:
                raise HTTPException(409, {"message": "automatic flag-4 cells have not been looked at by a person",
                                          "unreviewed_flag4": unreviewed,
                                          "hint": "change or confirm them, or send accept_unreviewed_flag4=true"})
            accepted = bool(unreviewed) and accept_unreviewed_flag4
            cur.execute("SELECT COALESCE(max(q.qc_flag_id), 0) FROM qc_flag q JOIN observation o USING (observation_id)"
                        " WHERE o.dataset_id = %s", (d["id"],))
            version = cur.fetchone()[0]
        cur.execute("INSERT INTO review (dataset_id, reviewer_id, decision, comment, decided_at, review_version,"
                    " unreviewed_flag4, accepted_unreviewed_flag4) VALUES (%s, %s, %s, %s, now(), %s, %s, %s)"
                    " RETURNING review_id", (d["id"], principal.user_id, decision, comment, version, unreviewed, accepted))
        review_id = cur.fetchone()[0]
        new_status = {"approved": "approved", "rejected": "rejected", "needs_work": "in_review"}[decision]
        if new_status != d["status"]:
            cur.execute("UPDATE dataset SET status = %s, updated_at = now() WHERE dataset_id = %s", (new_status, d["id"]))
        audit(cur, principal, {"approved": "dataset.approve", "rejected": "dataset.reject",
                               "needs_work": "dataset.needs_work"}[decision], key, {"status": d["status"]},
              {"status": new_status, "review_id": review_id, "review_version": version, "unreviewed_flag4": unreviewed,
               "accepted_unreviewed_flag4": accepted}, comment)
    out = {"dataset_key": key, "decision": decision, "status": new_status, "review_id": review_id,
           "review_version": version, "unreviewed_flag4_accepted": unreviewed if accepted else 0}
    if decision == "approved":
        if s["publish_on_approval"]:
            try:
                out["publish"] = {"ok": True, **publish(key, principal)}
            except HTTPException as exc:
                out["publish"] = {"ok": False, "error": exc.detail, "status": "approved",
                                  "hint": f"retry with POST /datasets/{key}/publish"}
        else:
            out["publish"] = {"ok": False, "error": "publish-on-approval is off", "status": "approved"}
    return out


# ----------------------------------------------------------------- publish --

def publish(key: str, principal) -> dict:
    s = settings()
    with tx() as cur:
        d = get_dataset(cur, key)
        if d["status"] == "published":
            raise HTTPException(409, "already published")
        if d["status"] != "approved":
            raise HTTPException(409, f"only an approved dataset can be published (status is {d['status']})")
        cur.execute("SELECT review_id, review_version, reviewer_id FROM review WHERE dataset_id = %s AND decision = 'approved'"
                    " ORDER BY review_id DESC LIMIT 1", (d["id"],))
        review_id, version, reviewer_id = cur.fetchone()
        cur.execute("SELECT bucket FROM storage_zone WHERE zone = 'standardised'")
        bucket = cur.fetchone()[0]
        cur.execute("SELECT source_record, variable_name, level_index, flag FROM qc_flag_effective_at(%s, %s)"
                    " WHERE set_by_user_id IS NOT NULL", (d["id"], version))
        human = cur.fetchall()
        cur.execute("SELECT username FROM app_user_public WHERE user_id = %s", (reviewer_id,))
        reviewer = (cur.fetchone() or ("unknown",))[0]
        cur.execute("SELECT after_state->>'config_version' FROM audit_log WHERE action = 'qc.run' AND entity_id = %s"
                    " ORDER BY audit_id DESC LIMIT 1", (key,))
        cfg = (cur.fetchone() or (None,))[0]
    staged = stagingcache.load(s["staging_dir"], key)
    meta = {"scats_review_decision": "approved", "scats_reviewer": reviewer, "scats_review_id": review_id,
            "scats_review_version": int(version), "scats_qc_config_version": cfg or "unknown",
            "scats_human_flag_decisions": len(human), "scats_published_by": principal.username,
            "scats_published_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    final = for_zarr(apply_human_flags(staged, human), meta)
    platform_type = staged.attrs["platform_type"]
    try:
        res = publish_zarr(final, s3=S3Store.from_env(), bucket=bucket, platform_type=platform_type, dataset_key=key)
    except PublishError as exc:
        _publish_failed(key, principal, str(exc))
        raise HTTPException(502, f"publish failed; the dataset stays 'approved'. {exc}")
    from scats_ingest.staging import staging_path
    try:
        with tx() as cur:
            d = get_dataset(cur, key, lock="update")
            if d["status"] != "approved":
                raise HTTPException(409, f"status changed while publishing ({d['status']})")
            cur.execute("INSERT INTO dataset_store (dataset_id, review_id, zone, store_uri, zarr_format, content_sha256,"
                        " manifest_sha256, n_objects, total_bytes, review_version, qc_config_version, published_by,"
                        " staging_path, staging_superseded_at) VALUES (%s,%s,'standardised',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())",
                        (d["id"], review_id, res.uri, res.zarr_format, res.content_sha256, res.manifest_sha256,
                         res.n_objects, res.total_bytes, version, cfg, principal.user_id,
                         str(staging_path(s["staging_dir"], key))))
            cur.execute("UPDATE dataset SET status = 'published', updated_at = now() WHERE dataset_id = %s", (d["id"],))
            audit(cur, principal, "dataset.publish", key, {"status": "approved"},
                  {"status": "published", "store_uri": res.uri, "content_sha256": res.content_sha256,
                   "manifest_sha256": res.manifest_sha256, "review_version": int(version), "n_objects": res.n_objects},
                  "published to the standardised zone")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        msg = (f"the Zarr was written but the catalogue update failed ({type(exc).__name__}); the dataset stays 'approved'; "
               "retrying publish is safe (it replaces the objects)")
        _publish_failed(key, principal, msg)
        raise HTTPException(500, msg)
    check = verify_store_consistency(key, db_url=s["db_url"], staging_dir=s["staging_dir"], s3=S3Store.from_env())
    return {"status": "published", "store_uri": res.uri, "zarr_format": res.zarr_format, "n_objects": res.n_objects,
            "content_sha256": res.content_sha256, "review_version": int(version),
            "consistency_ok": check.ok, "consistency_problems": check.problems}


def _publish_failed(key, principal, message):
    try:
        with tx() as cur:
            d = get_dataset(cur, key)
            audit(cur, principal, "dataset.publish_failed", key, None, {"status": d["status"]}, message[:1000])
    except Exception:  # noqa: BLE001 - the failure is already being reported to the caller
        pass


# ------------------------------------------------------------------- audit --

def audit_trail(key: str, principal, page: int, size: int | None):
    size, offset = page_args(page, size)
    full = principal.at_least("reviewer")
    with tx() as cur:
        get_dataset(cur, key)
        cur.execute("SELECT audit_id, occurred_at, username, action, comment, before_state, after_state, count(*) OVER ()"
                    " FROM audit_log WHERE entity_type = 'dataset' AND entity_id = %s ORDER BY audit_id DESC"
                    " LIMIT %s OFFSET %s", (key, size, offset))
        rows = cur.fetchall()
    items = []
    for a, at, who, action, comment, before, after, _n in rows:
        item = {"audit_id": a, "occurred_at": at, "username": who, "action": action, "comment": comment}
        if full:
            item["before_state"], item["after_state"] = before, after
        items.append(item)
    return clean({"dataset_key": key, "page": page, "page_size": size, "total": rows[0][7] if rows else 0, "events": items})


def verify_audit() -> dict:
    with tx() as cur:
        cur.execute("SELECT audit_verify_chain(), (SELECT count(*) FROM audit_log)")
        broken, n = cur.fetchone()
    return {"ok": broken is None, "first_broken_audit_id": broken, "rows_checked": n}


PERSISTED_FLAGS = PERSISTED
