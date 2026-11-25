"""Entry point kept from Phase 1 (`uvicorn app.main:app`). The application now lives in access_api (Phase 4)."""
from access_api.main import app, check_database, check_storage  # noqa: F401
