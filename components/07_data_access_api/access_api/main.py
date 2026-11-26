"""Data access API (Phase 4). Runs as the database role scats_access, never as the superuser.

Every request goes through ONE dependency (`authorise`) that looks (method, route) up in policy.ROLE_MATRIX.
A route that is not in the table is refused, so a new endpoint is closed until someone gives it a role.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import boto3
import psycopg2
from botocore.client import Config
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

import scats_auth as auth
from scats_auth.web import COOKIE_NAME, csrf_ok, request_token

from . import service
from .db import tx
from .filters import Refused
from .policy import ROLE_MATRIX, SEED_WARNING, settings

log = logging.getLogger("scats.access")
VERSION = "0.3.0-phase5"
# The dashboard (component 08) is plain files served from here. Only the names in DASHBOARD_FILES are ever served.
DASHBOARD_DIR = Path(os.getenv("DASHBOARD_DIR") or Path(__file__).resolve().parents[2] / "08_visualization_dashboard" / "dashboard" / "static")
DASHBOARD_FILES = {"index.html": "text/html; charset=utf-8", "style.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                   "logic.js": "text/javascript; charset=utf-8", "api.js": "text/javascript; charset=utf-8", "svg.js": "text/javascript; charset=utf-8",
                   "views.js": "text/javascript; charset=utf-8"}
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def authorise(request: Request):
    route = request.scope.get("route")
    need_key = (request.method, getattr(route, "path", request.url.path))
    if need_key not in ROLE_MATRIX:
        raise HTTPException(403, "no access rule exists for this endpoint")
    need = ROLE_MATRIX[need_key]
    if need is None:
        return
    token, source = request_token(request)
    try:
        principal = auth.validate_token(settings()["db_url"], token)
    except psycopg2.OperationalError:
        raise HTTPException(503, "database unavailable")
    if principal is None:
        raise HTTPException(401, "login required", headers={"WWW-Authenticate": "Bearer"})
    if not csrf_ok(request, source):
        raise HTTPException(403, "missing X-Requested-With header (cookie sessions need it on changes)")
    if not principal.at_least(need):
        raise HTTPException(403, f"the {need} role is required")
    request.state.principal, request.state.token = principal, token


app = FastAPI(title="SCATS Data Access API", version=VERSION, dependencies=[Depends(authorise)],
              docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(Refused)
async def refused(_request: Request, exc: Refused):
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()")
    return resp


@app.on_event("startup")
def warn_default_passwords():
    try:
        names = auth.default_password_users(settings()["db_url"])
    except Exception:  # noqa: BLE001
        return
    if names:
        log.warning("%s (accounts: %s)", SEED_WARNING, ", ".join(names))


# ------------------------------------------------------------------ health --
# Phase 1 contract kept: GET /health is public and answers booleans only (no role names, no error text).

def _s3():
    return boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT") or None, aws_access_key_id=os.getenv("S3_ACCESS_KEY", "scatsdev"),
                        aws_secret_access_key=os.getenv("S3_SECRET_KEY", "scatsdev_secret_pw"), region_name="us-east-1",
                        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=3, retries={"max_attempts": 1}))


def check_database(full_audit: bool = False) -> dict:
    try:
        con = psycopg2.connect(settings()["db_url"], connect_timeout=3)
        try:
            with con.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN"
                            " ('dataset','observation','qc_flag','app_user','audit_log')")
                core = cur.fetchone()[0]
                if full_audit:
                    cur.execute("SELECT audit_verify_chain()")
                else:
                    cur.execute("SELECT audit_verify_recent(%s)", (int(os.getenv("HEALTH_AUDIT_WINDOW", "1000")),))
                broken = cur.fetchone()[0]
        finally:
            con.close()
        return {"ok": core == 5 and broken is None, "audit_chain_broken_at": broken}
    except Exception:  # noqa: BLE001 - a health check must never raise or leak the reason
        return {"ok": False}


def check_storage() -> dict:
    try:
        zones = [b for b in os.getenv("ZONE_BUCKETS", "raw,standardised,derived").split(",") if b]
        existing = {b["Name"] for b in _s3().list_buckets().get("Buckets", [])}
        return {"ok": not [b for b in zones if b not in existing]}
    except Exception:  # noqa: BLE001
        return {"ok": False}


@app.get("/health/live", tags=["health"])
def live() -> dict:
    return {"status": "ok"}


DEFAULT_PASSWORDS_CACHE_S = 60            # how long a successful "do the seed passwords still work" answer is reused
_dp_cache: dict = {"at": None, "value": None}


def _reset_default_password_cache() -> None:
    _dp_cache.update(at=None, value=None)


def _is_loopback(request: Request) -> bool:
    """True only when the TCP peer is this machine AND the request was not relayed.
    The peer is read from the connection, never from a header. A reverse proxy on the same host also connects from loopback, so a
    request that carries any forwarding header (X-Forwarded-*, Forwarded, X-Real-IP) is treated as coming from outside."""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1"):
        return False
    return not any(k.startswith("x-forwarded-") or k in ("forwarded", "x-real-ip") for k in (h.lower() for h in request.headers))


def _default_passwords(request: Request) -> bool | None:
    """True/False only for a caller on a loopback address (the operator at the console); everyone else, and any failure, gets None.
    The yes/no is cached for DEFAULT_PASSWORDS_CACHE_S, so a flood of /health calls makes one database call. A failed lookup is not cached."""
    if not _is_loopback(request):
        return None
    now = time.monotonic()
    if _dp_cache["at"] is not None and now - _dp_cache["at"] < DEFAULT_PASSWORDS_CACHE_S:
        return _dp_cache["value"]
    try:
        value = bool(auth.default_password_users(settings()["db_url"]))
    except Exception:  # noqa: BLE001
        return None
    _dp_cache.update(at=now, value=value)
    return value


@app.get("/health", tags=["health"])
def health(request: Request) -> dict:
    db, storage = check_database(), check_storage()
    return {"status": "ok" if db["ok"] and storage["ok"] else "degraded", "service": "scats-data-access-api", "version": VERSION,
            "database": {"ok": db["ok"]}, "storage": storage, "default_passwords": _default_passwords(request)}


# --------------------------------------------------------------- dashboard --

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app", status_code=307)


def _page(name: str) -> FileResponse:
    if name not in DASHBOARD_FILES or not (DASHBOARD_DIR / name).is_file():
        raise HTTPException(404, "not found")
    return FileResponse(DASHBOARD_DIR / name, media_type=DASHBOARD_FILES[name], headers={"Content-Security-Policy": PAGE_CSP})


@app.get("/app", include_in_schema=False)
def dashboard():
    return _page("index.html")


@app.get("/app/{name}", include_in_schema=False)
def dashboard_file(name: str):
    return _page(name)


@app.get("/health/audit", tags=["health"])
def health_audit() -> dict:
    db = check_database(full_audit=True)
    return {"status": "ok" if db["ok"] else "broken", "database": db}


# -------------------------------------------------------------------- auth --

class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@app.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    ip = request.client.host if request.client else None
    try:
        token, p, expires = auth.login(settings()["db_url"], body.username, body.password, ip)
    except auth.AuthLocked as exc:
        raise HTTPException(429, str(exc))
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    except psycopg2.OperationalError:
        raise HTTPException(503, "database unavailable")
    s = auth.AuthSettings.from_env()
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=settings()["cookie_secure"], max_age=s.session_ttl_seconds, path="/")
    return {"token": token, "token_type": "bearer", "expires_at": expires, "user": {"username": p.username, "role": p.role}}


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    auth.logout(settings()["db_url"], request.state.token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request):
    p = request.state.principal
    return {"username": p.username, "role": p.role}


# --------------------------------------------------------------- catalogue --

@app.get("/catalogue", tags=["catalogue"])
def catalogue(request: Request):
    """Phase 1 route. Now: published datasets only, and only for signed-in users."""
    p = request.state.principal
    with tx() as cur:
        lim = service.limits_for(cur, p.role)
        service.check_rate(cur, p, lim)
        cur.execute("SELECT dataset_key, title, dataset_type, source, is_synthetic FROM catalogue_dataset ORDER BY dataset_key LIMIT %s",
                    (lim["page_size_max"],))
        rows = [dict(zip(("dataset_key", "title", "dataset_type", "source", "is_synthetic"), r)) for r in cur.fetchall()]
        service.write_log(cur, p, action="discover", target_type="dataset", flt={"route": "/catalogue"}, n_rows=len(rows))
    return {"count": len(rows), "datasets": rows}


@app.get("/datasets")
def list_datasets(request: Request):
    return service.discover(request.state.principal, "dataset", request.query_params)


@app.get("/products")
def list_products(request: Request):
    return service.discover(request.state.principal, "product", request.query_params)


def _version(request: Request) -> str | None:
    return request.query_params.get("version")


@app.get("/datasets/{key}")
def dataset(key: str, request: Request):
    return service.metadata(request.state.principal, "dataset", key, _version(request))


@app.get("/products/{key}")
def product(key: str, request: Request):
    return service.metadata(request.state.principal, "product", key, _version(request))


@app.get("/datasets/{key}/zarr")
def dataset_zarr(key: str, request: Request):
    return service.zarr_listing(request.state.principal, "dataset", key, _version(request))


@app.get("/products/{key}/zarr")
def product_zarr(key: str, request: Request):
    return service.zarr_listing(request.state.principal, "product", key, _version(request))


@app.get("/datasets/{key}/subset")
def dataset_subset(key: str, request: Request):
    return service.subset(request.state.principal, "dataset", key, request.query_params)


@app.get("/products/{key}/subset")
def product_subset(key: str, request: Request):
    return service.subset(request.state.principal, "product", key, request.query_params)


def _file(result) -> Response:
    data, mime, name, headers = result
    return Response(content=data, media_type=mime, headers={**headers, "Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/datasets/{key}/download")
def dataset_download(key: str, request: Request):
    return _file(service.download(request.state.principal, "dataset", key, request.query_params))


@app.get("/products/{key}/download")
def product_download(key: str, request: Request):
    return _file(service.download(request.state.principal, "product", key, request.query_params))


# ------------------------------------------------------------------ limits --

@app.get("/limits")
def limits(request: Request):
    return service.show_limits(request.state.principal)


class NewLimit(BaseModel):
    value: int = Field(ge=1, le=10**10)


@app.put("/admin/limits/{role}/{name}")
def set_limit(role: str, name: str, body: NewLimit, request: Request):
    try:
        return service.set_limit(request.state.token, role, name, body.value)
    except psycopg2.Error as exc:
        raise HTTPException(403, str(exc)[:150])
