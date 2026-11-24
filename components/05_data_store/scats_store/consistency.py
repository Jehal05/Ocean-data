"""Compare a published Zarr with what PostgreSQL says it should hold (staging data + the review snapshot's flags)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg2

from scats_common.flags import PERSISTED
from scats_ingest import rules
from scats_ingest.staging import read_staging

from .checksum import content_sha256, datasets_equal
from .reader import StoreCorrupt, read_store
from .s3 import S3Store


@dataclass
class StoreConsistency:
    dataset_key: str
    ok: bool
    problems: list = field(default_factory=list)
    human_rows_checked: int = 0
    flag_rows_checked: int = 0


def verify_store_consistency(dataset_key: str, *, db_url: str, staging_dir: Path | str, s3: S3Store) -> StoreConsistency:
    problems: list[str] = []
    con = psycopg2.connect(db_url)
    try:
        with con.cursor() as cur:
            cur.execute("SELECT d.dataset_id, s.store_uri, s.zone, s.review_version, s.content_sha256, s.manifest_sha256"
                        " FROM dataset d JOIN dataset_store s USING (dataset_id) WHERE d.dataset_key = %s", (dataset_key,))
            row = cur.fetchone()
            if row is None:
                return StoreConsistency(dataset_key, False, ["dataset has no published store"])
            did, uri, _zone, version, content, manifest = row
            cur.execute("SELECT bucket FROM storage_zone WHERE zone = 'standardised'")
            bucket = cur.fetchone()[0]
            cur.execute("SELECT source_record, variable_name, level_index, flag, set_by_user_id FROM"
                        " qc_flag_effective_at(%s, %s)", (did, version))
            eff = cur.fetchall()
    finally:
        con.close()
    prefix = uri.split(f"s3://{bucket}/", 1)[1]
    try:
        z = read_store(s3, bucket, prefix, expected_manifest_sha256=manifest)
    except (StoreCorrupt, Exception) as exc:  # noqa: BLE001
        return StoreConsistency(dataset_key, False, [f"store unreadable or corrupt: {type(exc).__name__}: {exc}"])
    if content_sha256(z) != content:
        problems.append("content checksum differs from the catalogue")
    st = read_staging(staging_dir, dataset_key)
    data_vars = [n for n in z.variables if not n.endswith("_qc")]
    diff = datasets_equal(st[[n for n in data_vars if n in st.variables]], z[data_vars])
    problems += [f"data differs from staging: {d}" for d in diff[:5]]

    kind = z.attrs["dataset_type"]
    human = [(r, v, lvl, f) for r, v, lvl, f, u in eff if u is not None]
    if kind in ("profile", "timeseries"):
        for r, v, lvl, f in human:                          # every person's decision is in the Zarr
            q = z[f"{v}_qc"].values
            got = int(q[r, lvl]) if (kind == "profile" and v not in ("position", "time")) else int(q[r])
            if got != f:
                problems.append(f"human flag {f} at record {r} {v}[{lvl}] but Zarr holds {got}")
    want = {x.key: x.flag for x in rules.expected_rows(z)}                      # persisted flags found in the Zarr
    have = {(r, v, lvl): f for r, v, lvl, f, _u in eff if f in PERSISTED}       # persisted flags the database holds
    for k in sorted(set(want) | set(have)):
        if want.get(k) != have.get(k):
            problems.append(f"flag at {k}: Zarr {want.get(k)} vs database {have.get(k)}")
    return StoreConsistency(dataset_key, not problems, problems[:20], len(human), len(have))
