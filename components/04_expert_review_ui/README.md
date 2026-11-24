# 04 Expert Review Interface

**Phase 3, built.** `review_api/` is a FastAPI service (login, role-checked routes, flag changes, review decisions, publish, audit). `static/` is a small web UI in plain JavaScript that writes text nodes only. Authorisation is server-side, from the role matrix in `review_api/policy.py`; an unlisted route is refused.

Run: see the Phase 3 section of the top-level README. The Dockerfile is not built (no Docker daemon here). The UI was exercised only as described in `docs/phase3_design.md`.
