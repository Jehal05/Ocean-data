"""Accounts and sessions on top of the auth_* functions of db/init/006_review_auth.sql.

The password check, the rate limit and the audit rows all happen INSIDE PostgreSQL. This module only creates the
random token, hashes it (the database never sees the token itself), and calls the functions. No password or token is
logged or returned from here except the token that login() hands to the person who just logged in.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass

import psycopg2

ROLE_ORDER = {"viewer": 1, "reviewer": 2, "admin": 3}


class AuthError(Exception):
    """Wrong or missing credentials."""


class AuthLocked(AuthError):
    """Too many failed attempts for this user or address."""


class AuthDenied(Exception):
    """Valid token, role not allowed."""


class AdminError(Exception):
    """An admin action was refused by the database (message is safe to show)."""

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind                              # invalid | not_found | conflict | forbidden


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    role: str

    def at_least(self, role: str) -> bool:
        return ROLE_ORDER.get(self.role, 0) >= ROLE_ORDER[role]


@dataclass(frozen=True)
class AuthSettings:
    session_ttl_seconds: int = 8 * 3600
    max_failures_user: int = 5
    max_failures_ip: int = 20
    window_seconds: int = 15 * 60

    @classmethod
    def from_env(cls) -> AuthSettings:
        g = os.getenv
        return cls(int(g("AUTH_SESSION_TTL_SECONDS", 8 * 3600)), int(g("AUTH_MAX_FAILURES_USER", 5)),
                   int(g("AUTH_MAX_FAILURES_IP", 20)), int(g("AUTH_WINDOW_SECONDS", 15 * 60)))


def new_token() -> str:
    return secrets.token_urlsafe(32)                  # 256 bits


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _connect(db_url: str):
    return psycopg2.connect(db_url, connect_timeout=5)


def login(db_url: str, username: str, password: str, ip: str | None, settings: AuthSettings | None = None):
    """Returns (token, Principal, expires_at). Raises AuthLocked or AuthError (one message for every kind of failure)."""
    s = settings or AuthSettings.from_env()
    token = new_token()
    con = _connect(db_url)
    try:
        with con, con.cursor() as cur:                # commits also when the outcome is "denied" (the attempt is kept)
            cur.execute("SELECT * FROM auth_login(%s, %s, %s, %s, %s, %s, %s, %s)",
                        (username, password, ip, hash_token(token), s.session_ttl_seconds, s.max_failures_user,
                         s.max_failures_ip, s.window_seconds))
            outcome, uid, uname, role, exp = cur.fetchone()
    finally:
        con.close()
    if outcome == "locked":
        raise AuthLocked("too many failed attempts; try again later")
    if outcome != "ok":
        raise AuthError("wrong username or password, or the account is disabled")
    return token, Principal(uid, uname, role), exp


def validate_token(db_url: str, token: str | None) -> Principal | None:
    if not token:
        return None
    con = _connect(db_url)
    try:
        with con, con.cursor() as cur:
            cur.execute("SELECT * FROM auth_validate_token(%s)", (hash_token(token),))
            row = cur.fetchone()
    finally:
        con.close()
    return Principal(*row) if row else None


def logout(db_url: str, token: str) -> bool:
    con = _connect(db_url)
    try:
        with con, con.cursor() as cur:
            cur.execute("SELECT auth_logout(%s)", (hash_token(token),))
            return bool(cur.fetchone()[0])
    finally:
        con.close()


def _admin_call(db_url: str, sql: str, params: tuple):
    con = _connect(db_url)
    try:
        with con, con.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    except psycopg2.Error as exc:
        code = exc.pgcode
        msg = (exc.diag.message_primary or "refused").strip()
        if code == "42501":
            raise AuthDenied("admin role required") from exc
        if code == "P0002":
            raise AdminError(msg, "not_found") from exc
        if code == "23505":
            raise AdminError("that username already exists", "conflict") from exc
        if code == "22023":
            raise AdminError(msg, "invalid") from exc
        raise
    finally:
        con.close()


def create_user(db_url, admin_token, username, display_name, password, role) -> int:
    return _admin_call(db_url, "SELECT auth_admin_create_user(%s,%s,%s,%s,%s)",
                       (hash_token(admin_token), username, display_name, password, role))[0]


def set_active(db_url, admin_token, username, active: bool) -> None:
    _admin_call(db_url, "SELECT auth_admin_set_active(%s,%s,%s)", (hash_token(admin_token), username, active))


def reset_password(db_url, admin_token, username, new_password) -> None:
    _admin_call(db_url, "SELECT auth_admin_reset_password(%s,%s,%s)", (hash_token(admin_token), username, new_password))


def set_role(db_url, admin_token, username, role) -> None:
    _admin_call(db_url, "SELECT auth_admin_set_role(%s,%s,%s)", (hash_token(admin_token), username, role))


def default_password_users(db_url: str) -> list[str]:
    """Development accounts that still have their published password (deployment check)."""
    con = _connect(db_url)
    try:
        with con, con.cursor() as cur:
            cur.execute("SELECT auth_default_password_users()")
            return list(cur.fetchone()[0])
    finally:
        con.close()
