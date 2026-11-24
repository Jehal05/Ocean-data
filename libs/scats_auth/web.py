"""Token handling for FastAPI services: Bearer header or session cookie, with a CSRF guard for cookies."""
from __future__ import annotations

from fastapi import Request

from .core import Principal, validate_token

COOKIE_NAME = "scats_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "scats-ui"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def bearer_token(request: Request) -> str | None:
    """Only the Authorization header (services on another port must not accept the review UI's cookie)."""
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") and h[7:].strip() else None


def request_token(request: Request) -> tuple[str | None, str]:
    """(token, source) where source is 'header', 'cookie' or 'none'. A Bearer header wins over the cookie."""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer ") and h[7:].strip():
        return h[7:].strip(), "header"
    c = request.cookies.get(COOKIE_NAME)
    return (c, "cookie") if c else (None, "none")


def csrf_ok(request: Request, source: str) -> bool:
    """A cookie is sent by the browser on cross-site requests too; a custom header is not (no CORS is enabled).
    So an unsafe request authenticated by cookie must carry the header. Bearer-header requests need nothing."""
    if source != "cookie" or request.method in SAFE_METHODS:
        return True
    return request.headers.get(CSRF_HEADER, "") == CSRF_VALUE


def principal_from_request(request: Request, db_url: str) -> tuple[Principal | None, str]:
    token, source = request_token(request)
    return validate_token(db_url, token), source
