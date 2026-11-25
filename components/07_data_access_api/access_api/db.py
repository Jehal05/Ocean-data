from __future__ import annotations

from contextlib import contextmanager

import psycopg2
from fastapi import HTTPException

from .policy import settings


@contextmanager
def tx():
    """One transaction: commit on success, roll back on any error. Connections are not pooled (proof of concept)."""
    try:
        con = psycopg2.connect(settings()["db_url"], connect_timeout=5)
    except psycopg2.OperationalError:
        raise HTTPException(503, "database unavailable")
    try:
        with con:
            with con.cursor() as cur:
                yield cur
    except psycopg2.errors.InsufficientPrivilege as exc:
        raise HTTPException(403, f"refused by the database: {(exc.diag.message_primary or '').strip()}")
    except psycopg2.errors.CheckViolation as exc:
        raise HTTPException(422, f"refused by the database: {(exc.diag.message_primary or '').strip()}")
    except psycopg2.OperationalError:
        raise HTTPException(503, "database unavailable")
    finally:
        con.close()
