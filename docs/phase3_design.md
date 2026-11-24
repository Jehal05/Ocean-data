# Phase 3 design: accounts, expert review, audit trail, Zarr store

Status: written before the code, then kept true (sections marked "as built" were checked against the code at the end).
Scope: accounts and roles, the review workflow (API plus a small web UI), audit wiring, and the Zarr store for approved data.
Out of scope: derived products (Phase 4), discovery/subsetting/download API (Phase 4), dashboard (Phase 5), SSO/LDAP, the sediment database.

## 1. Choices made (the prompt left these open)

| Question | Choice | Why |
|---|---|---|
| Where do accounts live | New library `libs/scats_auth`, endpoints in the new `review-api`; `parser-api` uses the same library to check tokens on `/ingest` | Two services need to validate the same tokens; one library avoids two copies of security code |
| Who checks passwords | PostgreSQL `SECURITY DEFINER` functions (`auth_login`, `auth_validate_token`, `auth_admin_*`), bcrypt through pgcrypto | The service roles never read `app_user.password_hash` (not even `scats_review`), and admin actions re-check the admin token inside the database |
| Rate limiting | In the database (`login_attempt` table), per username (5 failures) and per IP (20) in a 15 minute window | Shared by every process; survives restarts. Behind a proxy the IP is the proxy's |
| Publish on approval or explicit | **Approval records the decision; publishing runs right after in a second step** and can be retried with `POST /datasets/{key}/publish` | The DB decision must stay valid when object storage is down; a retry is safe |
| Zarr format | **Zarr format 2** written by zarr-python 3.4.2 (`zarr_format=2`), Blosc/zstd | Widest tool support (Kerchunk, older xarray, GDAL), mature string and datetime handling. v3 is a one-parameter change later |
| Chunking | Profiles: whole profile rows, about 8 MiB per chunk; series: about 8 MiB along time; grids: time slices x at most 512x512 tiles. Hard cap 50 MiB (asserted) | Reads follow the access pattern: one profile, one time window, one map tile |
| Cell-level overrides on grids | **Not supported** (confirm only) | SST/bathymetry are stored as per-slice summary rows (Phase 2). A cell override would need a per-cell table |
| Kerchunk / VirtualiZarr | **Deferred** | They index existing files; Phase 3 has no large external files. Revisit in Phase 4 if subsetting needs it |

## 2. Review state machine

`dataset.status` values exist since Phase 1. Transitions and who may make them (enforced by the API and by the database trigger `dataset_transition_guard`):

| From | To | Made by | Trigger |
|---|---|---|---|
| (new) | ingested / qc_running / qc_done | scats_ingest | ingest (Phase 2) |
| qc_done | in_review | scats_review | `POST /review/start` |
| in_review | in_review | scats_review | decision `needs_work` (stays; recorded in `review`) |
| in_review | approved | scats_review | decision `approved` |
| in_review | rejected | scats_review | decision `rejected` (terminal) |
| approved | published | scats_review | successful Zarr publish |

Everything else is refused (HTTP 409 from the API, `insufficient_privilege` from the trigger). Flag changes and confirmations need `in_review`. A dataset in `in_review` can be re-ingested (same file): automatic flags may change, human decisions stay. `approved`, `rejected`, `published` refuse re-ingest.

"Reviewed" means: the cell's current flag row was written by a person (a change, or a **confirm**, which re-states the same flag). Approving with automatic flag-4 cells nobody has looked at is refused unless the request sets `accept_unreviewed_flag4=true`; the count and the choice are written to `review` and `audit_log`.

## 3. Roles

| Role | Can |
|---|---|
| viewer | read datasets, flags, cells, the dataset audit trail (without before/after states) |
| reviewer | viewer + start review, change/confirm flags, approve/reject/needs_work, publish |
| admin | reviewer + manage users, read full audit, verify the audit chain, ingest (admin token) |

The matrix is one table in `app/policy.py`. A global dependency looks every request up in it and **denies anything not listed**. `REVIEW_FOUR_EYES=true` (default): nobody can approve a dataset that the same username ingested (`dataset.ingest` audit rows). `false` turns it off.

## 4. Flags

* A flag only moves by adding a `qc_flag` row. A human row has `set_by_user_id`, `test_id='expert_review'` and a non-empty comment (also enforced by trigger `qc_flag_human_guard`). Humans may set 1, 2, 3 or 4 only; cells with flag 9 (missing) and padding levels cannot be changed.
* `qc_flag_current` (redefined in migration 006): **a human row beats any automatic row**, newest wins inside each class (by `qc_flag_id`, not by timestamp). `qc_flag_auto_current` is the newest automatic row. Result: a QC re-run can never undo an override.
* Cell that stops being flagged after a QC config change (open question from Phase 2): the re-run writes a **cleared row** (flag taken from the new staging array, normally 1, same test id, `detail.cleared_by_rerun=true`). The old row stays as history. If a human row exists the override stays; the cell view shows the new automatic flag next to it.
* Snapshot: approval stores `review_version = max(qc_flag_id)` of the dataset. `qc_flag_effective_at(dataset_id, version)` returns the effective flags at that version; publishing and the Zarr consistency check use only that.
* Concurrency: flag changes take a shared lock on the dataset row, approval takes an exclusive one. Two reviewers on one cell: both rows kept, the later id wins, both audited. No per-user locking.

## 5. Audit events

`auth.login`, `auth.login_failed`, `auth.login_blocked`, `auth.logout`, `user.create`, `user.disable`, `user.enable`, `user.reset_password`, `user.set_role`, `review.start`, `flag.change`, `flag.confirm`, `dataset.approve`, `dataset.reject`, `dataset.needs_work`, `dataset.publish`, `dataset.publish_failed`, plus the Phase 2 `dataset.ingest` and `qc.run`. A flag change and its audit row are one transaction; so are decisions and status changes. Passwords and tokens are never in an audit row. `audit_verify_chain()` checks the hash chain; `GET /audit/verify` exposes it to admins.

## 6. Zarr layout and failure behaviour (as built)

```
standardised/<platform_type>/<dataset_key>.zarr/   (zarr format 2, consolidated metadata)
    data variables, <variable>_qc arrays, coordinates, scats_manifest.json
    attrs: dataset attrs + scats_review_decision, scats_reviewer, scats_review_id,
           scats_review_version, scats_qc_config_version, scats_published_at
```

Write order: build dataset (staging + human overrides at the snapshot) -> write Zarr to a local temp dir -> verify (arrays equal to source, content checksum) -> upload to `standardised/.tmp/<uuid>/...` -> download and verify every object -> copy to the final prefix -> write the manifest last -> delete the tmp prefix -> then, in one DB transaction, insert `dataset_store`, set `published`, audit. A failure before the final copy leaves the final prefix and the catalogue untouched. A failure after the copy but before the DB commit leaves objects without a catalogue row; the dataset stays `approved` and a retry first removes them. The response and the audit row say which case happened. Raw is never modified. Rejected datasets are never written. The NetCDF staging file is kept; `dataset_store.staging_superseded_at` marks it superseded.

## 7. Assumptions and limits

* No SSO/LDAP: passwords are held in the database. The dev seed users (`admin123`, `reviewer123`, `viewer123`) are unsafe; the login page and `/health` warn while they still work.
* No confidentiality classes: every reader sees every dataset (open point for NPOL).
* Cell overrides are not available for gridded datasets; no per-user locking; the page size is 100 (max 200) and a flag batch is capped at 500 cells.
* Reading Zarr from S3 copies the objects to a temp directory first (no lazy remote read yet).
* Tested on PostgreSQL 16 without PostGIS and on moto, not on SeaweedFS. The UI was driven once in headless Chrome (see the final report), not across browsers.

## 8. Open points for NPOL

1. Their own login system (SSO/LDAP) and password policy. 2. Confidentiality classes and who may read what. 3. Who may approve what (per source, per platform type), and whether a second approver is needed. 4. Which flags a reviewer may set (we allow 1 to 4; 5 and 8 would mean the value was changed and need a value-correction workflow). 5. Retention for rejected datasets.
