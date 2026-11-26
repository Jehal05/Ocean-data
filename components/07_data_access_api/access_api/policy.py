"""Who may call what, and the limits users are told about. ONE table; anything not in it is refused (403)."""
from __future__ import annotations

import os

# (HTTP method, route path template) -> minimum role. None = public (no data behind it).
ROLE_MATRIX: dict[tuple[str, str], str | None] = {
    ("GET", "/health/live"): None,
    ("GET", "/health"): None,                    # Phase 1 contract: booleans only, no names, no error text
    ("GET", "/health/audit"): "admin",
    ("GET", "/"): None,                          # Phase 5: redirects to the dashboard
    ("GET", "/app"): None,                       # Phase 5: the dashboard page and its fixed list of files (no data behind them)
    ("GET", "/app/{name}"): None,
    ("POST", "/auth/login"): None,
    ("POST", "/auth/logout"): "viewer",
    ("GET", "/auth/me"): "viewer",
    ("GET", "/catalogue"): "viewer",
    ("GET", "/datasets"): "viewer",
    ("GET", "/datasets/{key}"): "viewer",
    ("GET", "/datasets/{key}/zarr"): "viewer",
    ("GET", "/datasets/{key}/subset"): "viewer",
    ("GET", "/datasets/{key}/download"): "viewer",
    ("GET", "/products"): "viewer",
    ("GET", "/products/{key}"): "viewer",
    ("GET", "/products/{key}/zarr"): "viewer",
    ("GET", "/products/{key}/subset"): "viewer",
    ("GET", "/products/{key}/download"): "viewer",
    ("GET", "/limits"): "viewer",
    ("PUT", "/admin/limits/{role}/{name}"): "admin",
}

DEFAULT_LIMITS = {
    "page_size_max": 200, "max_rows_subset": 5000, "max_rows_download": 500000, "max_bytes_download": 104857600,
    "max_time_span_days": 366, "max_bbox_area_deg2": 10000, "request_timeout_s": 30, "rate_per_minute": 60,
}
HARD_MAX = {"page_size_max": 1000, "max_rows_subset": 20000, "max_rows_download": 5000000, "max_bytes_download": 1073741824,
            "max_time_span_days": 3660, "max_bbox_area_deg2": 64800, "request_timeout_s": 300, "rate_per_minute": 6000}
LIMIT_TEXT = {
    "page_size_max": "rows per JSON page", "max_rows_subset": "rows one subset call may select (refused above, never cut)",
    "max_rows_download": "rows one download may select", "max_bytes_download": "size of the produced file",
    "max_time_span_days": "time range of one request (a request without a time filter on a longer collection is refused)",
    "max_bbox_area_deg2": "area of the bounding box in square degrees", "request_timeout_s": "time budget; checked between reads, a running read is not cancelled",
    "rate_per_minute": "requests per user per minute (HTTP 429)",
}
# Statements shown to users and checked by tests/check_phase4.py
LIMITS_STATEMENTS = [
    "Over a limit the answer is 413, 422 or 429 with the limit and the value; a result is never cut silently.",
    "A page that is not the last says truncated=true and next_page.",
    "Time budget (request_timeout_s) is checked between reads: a read that is already running is not cancelled.",
    "Refused requests (bad filter, over a limit) are not written to the query log; only answered requests are.",
    "Dataset extents come from the dataset attributes as numbers; a dataset that crosses 180 degrees longitude is listed by its min/max and may match more boxes than it should.",
    "NetCDF and CSV downloads hold the same rows (one dimension 'row'); gridded data is not rebuilt into a grid.",
]

SEED_WARNING = ("Development accounts admin, reviewer and viewer still have their published passwords. "
                "Anyone who has read the repository can log in. Change them before any real use.")


def settings() -> dict:
    g = os.getenv
    return {
        "db_url": g("DATABASE_URL", "postgresql://scats_access:scats_access_dev_pw@localhost:5432/scats"),
        "cookie_secure": g("AUTH_COOKIE_SECURE", "false").lower() == "true",
        "source_bucket": g("STANDARDISED_BUCKET", "standardised"),
        "derived_bucket": g("DERIVED_BUCKET", "derived"),
    }
