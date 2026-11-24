# 02 Data Parser API

**Phase 2: built.**

| Part | Where |
|---|---|
| Parsers (plugin per format, `@register`) | `scats_parsers/` |
| YAML mappings (names, units, fill values, provider flags) | `scats_parsers/mappings/` |
| Ingest: parse, QC, staging file, PostgreSQL, audit, consistency check | `scats_ingest/` |
| FastAPI app | `app/main.py` |
| Image (build context is the repository root) | `Dockerfile` |

Endpoints (port 8001): `GET /health`, `GET /parsers`, `POST /ingest {"path": ...}`, `GET /datasets/{key}/qc-summary`.
`/ingest` reads only below `INGEST_ALLOWED_DIRS`, requires `X-API-Key` when `INGEST_API_KEY` is set, and is bound to localhost by compose. It is not real authentication; roles arrive with Phase 3.

**Add a format:** write one `Parser` subclass (`can_parse`, `parse`), add one YAML mapping, decorate with `@register`.

Run locally: `PYTHONPATH=libs:components/02_parser_api:components/03_qc_framework uvicorn app.main:app --app-dir components/02_parser_api` with `DATABASE_URL` pointing at a database that has `db/init/*.sql` applied.

Limits: SST and bathymetry get no spike or land test; parsers have only seen the synthetic files and the hand-built edge cases in `tests/unit/test_parsers.py`; real Argo, CTD and buoy files will find mapping gaps.
