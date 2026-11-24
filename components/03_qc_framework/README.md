# 03 QC Framework

**Phase 2.** Not built yet.

Will provide: automated tests (missing value, format, range, position, timestamp, instrument-specific, cross-variable), flag management, and a record of the test behind every flag. Tables already exist: `qc_test`, `qc_flag`, `qc_flag_current` (see `db/init/001_schema.sql`).

Acceptance target: catch every error listed in `data/test_errors/answer_key.csv` and leave the clean control records unflagged.
