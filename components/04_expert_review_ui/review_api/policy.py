"""Who may call what, and the limits users are told about. ONE table; anything not in it is refused."""
from __future__ import annotations

import os
from pathlib import Path

# (HTTP method, route path template) -> minimum role. None = public (no data behind it).
ROLE_MATRIX: dict[tuple[str, str], str | None] = {
    ("GET", "/health"): None,
    ("GET", "/auth/info"): None,
    ("POST", "/auth/login"): None,
    ("GET", "/"): None,
    ("GET", "/static/{name}"): None,
    ("POST", "/auth/logout"): "viewer",
    ("GET", "/auth/me"): "viewer",
    ("GET", "/datasets"): "viewer",
    ("GET", "/datasets/{dataset_key}"): "viewer",
    ("GET", "/datasets/{dataset_key}/flags"): "viewer",
    ("GET", "/datasets/{dataset_key}/cells/{observation_id}"): "viewer",
    ("GET", "/datasets/{dataset_key}/audit"): "viewer",
    ("POST", "/datasets/{dataset_key}/review/start"): "reviewer",
    ("POST", "/datasets/{dataset_key}/flags"): "reviewer",
    ("POST", "/datasets/{dataset_key}/flags/confirm"): "reviewer",
    ("POST", "/datasets/{dataset_key}/review"): "reviewer",
    ("POST", "/datasets/{dataset_key}/publish"): "reviewer",
    ("GET", "/audit/verify"): "admin",
    ("GET", "/admin/users"): "admin",
    ("POST", "/admin/users"): "admin",
    ("POST", "/admin/users/{username}/disable"): "admin",
    ("POST", "/admin/users/{username}/enable"): "admin",
    ("POST", "/admin/users/{username}/reset-password"): "admin",
    ("POST", "/admin/users/{username}/role"): "admin",
}

MAX_FLAG_BATCH = 500        # cells per request
PAGE_SIZE = 100             # default rows per page
PAGE_MAX = 200
HUMAN_FLAGS = (1, 2, 3, 4)
CONFIRMABLE = (2, 3, 4)
STATUSES = ("ingested", "qc_running", "qc_done", "in_review", "approved", "rejected", "published")

REVIEW_LIMITS = [
    f"A flag change or confirmation takes at most {MAX_FLAG_BATCH} cells; list pages hold at most {PAGE_MAX} rows.",
    "Cell-level flag changes are not available for gridded datasets (SST, bathymetry); they can be confirmed, approved or rejected as a whole.",
    "There is no per-user locking: if two reviewers change the same cell, both decisions are kept and the later one wins.",
    "People may set flags 1 to 4 only, and cannot change a missing (9) cell. Data values are never edited.",
    "observation.overall_flag keeps the automatic value; the effective flags are the ones listed under flags.",
    "The review service reads the staging NetCDF file; if it is missing, cell views and changes return 503.",
]

SEED_WARNING = ("Development accounts admin, reviewer and viewer still have their published passwords. "
                "Anyone who has read the repository can log in. Change them before any real use.")


def settings() -> dict:
    g = os.getenv
    return {
        "db_url": g("DATABASE_URL", "postgresql://scats_review:scats_review_dev_pw@localhost:5432/scats"),
        "staging_dir": Path(g("STAGING_DIR", "/srv/staging")),
        "four_eyes": g("REVIEW_FOUR_EYES", "true").lower() != "false",
        "publish_on_approval": g("PUBLISH_ON_APPROVAL", "true").lower() != "false",
        "cookie_secure": g("AUTH_COOKIE_SECURE", "false").lower() == "true",
    }
