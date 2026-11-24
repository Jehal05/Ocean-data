"""Review API (Phase 3). Runs as the database role scats_review, never as the superuser.

Every request goes through ONE dependency (`authorise`) that looks (method, route) up in policy.ROLE_MATRIX.
A route that is not in the table is refused, so a new endpoint is closed until someone gives it a role.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import psycopg2
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import scats_auth as auth
from scats_auth.web import COOKIE_NAME, csrf_ok, request_token

from . import service
from .db import tx
from .policy import MAX_FLAG_BATCH, ROLE_MATRIX, SEED_WARNING, settings

log = logging.getLogger("scats.review")
STATIC = Path(__file__).resolve().parent.parent / "static"
STATIC_FILES = {"app.js": "text/javascript", "style.css": "text/css"}


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


app = FastAPI(title="SCATS Review API", version="0.1.0", dependencies=[Depends(authorise)],
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if not request.url.path.startswith("/static"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.on_event("startup")
def warn_default_passwords():
    try:
        names = auth.default_password_users(settings()["db_url"])
    except Exception:  # noqa: BLE001 - database may still be starting; /health reports it
        return
    if names:
        log.warning("%s (accounts: %s)", SEED_WARNING, ", ".join(names))


# ----------------------------------------------------------------- public --

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", media_type="text/html")


@app.get("/static/{name}")
def static_file(name: str):
    if name not in STATIC_FILES:
        raise HTTPException(404, "not found")
    return FileResponse(STATIC / name, media_type=STATIC_FILES[name])


def _default_pw_flag() -> bool | None:
    try:
        return bool(auth.default_password_users(settings()["db_url"]))
    except Exception:  # noqa: BLE001
        return None


@app.get("/health")
def health():
    try:
        with tx() as cur:
            cur.execute("SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),"
                        " audit_verify_recent(200)")
            user, superuser, broken = cur.fetchone()
    except HTTPException:
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok" if not superuser and broken is None else "degraded", "database_role": user,
            "database_role_is_superuser": superuser, "audit_chain_ok_recent": broken is None,
            "default_dev_passwords_in_use": _default_pw_flag()}


@app.get("/auth/info")
def auth_info():
    flag = _default_pw_flag()
    return {"default_dev_passwords_in_use": flag, "warning": SEED_WARNING if flag else None}


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
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=settings()["cookie_secure"],
                        max_age=s.session_ttl_seconds, path="/")
    return {"token": token, "token_type": "bearer", "expires_at": expires,
            "user": {"username": p.username, "role": p.role}}


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    auth.logout(settings()["db_url"], request.state.token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request):
    p = request.state.principal
    return {"username": p.username, "role": p.role, "user_id": p.user_id}


# --------------------------------------------------------------- datasets --



@app.get("/datasets")
def datasets(status: str | None = None, page: int = 1, page_size: int | None = None):
    return service.list_datasets(status, page, page_size)


@app.get("/datasets/{dataset_key}")
def dataset(dataset_key: str):
    return service.dataset_detail(dataset_key)


@app.get("/datasets/{dataset_key}/flags")
def flags(dataset_key: str, variable: str | None = Query(None, max_length=64), flag: int | None = Query(None, ge=0, le=9),
          test: str | None = Query(None, max_length=64), reviewed: bool | None = None, page: int = 1,
          page_size: int | None = None):
    return service.list_flags(dataset_key, variable, flag, test, reviewed, page, page_size)


@app.get("/datasets/{dataset_key}/cells/{observation_id}")
def cell(dataset_key: str, observation_id: int, variable: str | None = Query(None, max_length=64),
         level_index: int | None = Query(None, ge=-1), window: int = 5):
    return service.cell_view(dataset_key, observation_id, variable, level_index, window)


class Cell(BaseModel):
    observation_id: int
    variable: str = Field(min_length=1, max_length=64)
    level_index: int = Field(-1, ge=-1)


class FlagChange(BaseModel):
    cells: list[Cell] = Field(min_length=1, max_length=MAX_FLAG_BATCH + 1)   # one over the cap, so the error names the cap
    flag: int
    comment: str = Field("", max_length=4000)


class Confirm(BaseModel):
    cells: list[Cell] = Field(min_length=1, max_length=MAX_FLAG_BATCH + 1)
    comment: str = Field("", max_length=4000)


class Decision(BaseModel):
    decision: Literal["approved", "rejected", "needs_work"]
    comment: str = Field("", max_length=4000)
    accept_unreviewed_flag4: bool = False


@app.post("/datasets/{dataset_key}/review/start")
def review_start(dataset_key: str, request: Request):
    return service.start_review(dataset_key, request.state.principal)


@app.post("/datasets/{dataset_key}/flags")
def flags_change(dataset_key: str, body: FlagChange, request: Request):
    return service.change_flags(dataset_key, request.state.principal, body.cells, body.flag, body.comment, "change")


@app.post("/datasets/{dataset_key}/flags/confirm")
def flags_confirm(dataset_key: str, body: Confirm, request: Request):
    return service.change_flags(dataset_key, request.state.principal, body.cells, 0, body.comment, "confirm")


@app.post("/datasets/{dataset_key}/review")
def review(dataset_key: str, body: Decision, request: Request):
    return service.decide(dataset_key, request.state.principal, body.decision, body.comment, body.accept_unreviewed_flag4)


@app.post("/datasets/{dataset_key}/publish")
def publish(dataset_key: str, request: Request):
    return service.publish(dataset_key, request.state.principal)


@app.get("/datasets/{dataset_key}/audit")
def dataset_audit(dataset_key: str, request: Request, page: int = 1, page_size: int | None = None):
    return service.audit_trail(dataset_key, request.state.principal, page, page_size)


@app.get("/audit/verify")
def audit_verify():
    return service.verify_audit()


# ------------------------------------------------------------------ admin --

class NewUser(BaseModel):
    username: str = Field(max_length=40)
    display_name: str = Field("", max_length=100)
    password: str = Field(max_length=200)
    role: Literal["viewer", "reviewer", "admin"]


class Password(BaseModel):
    password: str = Field(max_length=200)


class RoleChange(BaseModel):
    role: Literal["viewer", "reviewer", "admin"]


def _admin(fn, *args):
    try:
        return fn(settings()["db_url"], *args)
    except auth.AuthDenied as exc:
        raise HTTPException(403, str(exc))
    except auth.AdminError as exc:
        raise HTTPException({"invalid": 422, "not_found": 404, "conflict": 409}.get(exc.kind, 400), str(exc))


@app.get("/admin/users")
def users():
    defaults = set(auth.default_password_users(settings()["db_url"]))
    with tx() as cur:
        cur.execute("SELECT user_id, username, display_name, role_name, is_active FROM app_user_public ORDER BY username")
        rows = cur.fetchall()
    return {"users": [{"user_id": r[0], "username": r[1], "display_name": r[2], "role": r[3], "is_active": r[4],
                       "default_dev_password": r[1] in defaults} for r in rows]}


@app.post("/admin/users", status_code=201)
def user_create(body: NewUser, request: Request):
    uid = _admin(auth.create_user, request.state.token, body.username, body.display_name, body.password, body.role)
    return {"user_id": uid, "username": body.username, "role": body.role}


@app.post("/admin/users/{username}/disable")
def user_disable(username: str, request: Request):
    _admin(auth.set_active, request.state.token, username, False)
    return {"username": username, "is_active": False}


@app.post("/admin/users/{username}/enable")
def user_enable(username: str, request: Request):
    _admin(auth.set_active, request.state.token, username, True)
    return {"username": username, "is_active": True}


@app.post("/admin/users/{username}/reset-password")
def user_reset(username: str, body: Password, request: Request):
    _admin(auth.reset_password, request.state.token, username, body.password)
    return {"username": username, "sessions_revoked": True}


@app.post("/admin/users/{username}/role")
def user_role(username: str, body: RoleChange, request: Request):
    _admin(auth.set_role, request.state.token, username, body.role)
    return {"username": username, "role": body.role, "sessions_revoked": True}


@app.exception_handler(psycopg2.Error)
async def db_error(request: Request, exc: psycopg2.Error):
    log.error("database error: %s", type(exc).__name__)
    return JSONResponse({"detail": "database error"}, status_code=500)
