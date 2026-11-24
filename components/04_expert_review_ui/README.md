# 04 Expert Review Interface

**Phase 3.** Not built yet.

Will provide: web application for authorised reviewers to inspect observations and profiles, approve or reject datasets, change flags, add comments and document corrective actions. Every action goes to the append-only `audit_log` (already in the schema, with a hash chain and blocked UPDATE/DELETE/TRUNCATE). Roles `viewer`, `reviewer`, `admin` already exist.
