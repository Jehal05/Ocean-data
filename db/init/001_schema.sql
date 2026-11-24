-- SCATS POC :: Phase 1 database schema
-- Runs once, automatically, on first start of the PostGIS container
-- (mounted into /docker-entrypoint-initdb.d).
--
-- Design notes
--   * The Zarr store holds the per-level values and per-level flags.
--   * PostgreSQL holds the catalogue, observation-level metadata, a queryable
--     copy of every QC decision (per observation AND per variable, with the
--     test that set it), users/roles and an immutable audit log.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- lookups --

CREATE TABLE storage_zone (
    zone        TEXT PRIMARY KEY,
    bucket      TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
);
INSERT INTO storage_zone VALUES
  ('raw',          'raw',          'Original files exactly as received. Never modified.'),
  ('standardised', 'standardised', 'Common data model, passed QC and review.'),
  ('derived',      'derived',      'Analysis-ready products generated from validated data.');

-- Quality flag scheme (see docs/data_model.md section 6)
CREATE TABLE qc_flag_scheme (
    flag        SMALLINT PRIMARY KEY,
    meaning     TEXT NOT NULL,
    description TEXT NOT NULL
);
INSERT INTO qc_flag_scheme VALUES
  (0, 'no_qc',            'No quality control has been applied.'),
  (1, 'good',             'Passed all applied tests.'),
  (2, 'probably_good',    'Passed, but with a minor concern.'),
  (3, 'probably_bad',     'Failed a soft test; may be correctable. Excluded from products by default.'),
  (4, 'bad',              'Failed a hard test. Excluded from products.'),
  (5, 'value_changed',    'Value was changed by an expert reviewer.'),
  (8, 'estimated',        'Value is interpolated or estimated.'),
  (9, 'missing',          'Value is missing.');

-- Registry of QC tests, so every flag can name the test that set it.
CREATE TABLE qc_test (
    test_id     TEXT PRIMARY KEY,
    category    TEXT NOT NULL CHECK (category IN
                  ('format','missing','range','position','timestamp',
                   'instrument','cross_variable','expert')),
    severity    TEXT NOT NULL CHECK (severity IN ('hard','soft')),
    description TEXT NOT NULL
);
INSERT INTO qc_test VALUES
  ('missing_value',       'missing',        'hard', 'Value is null, NaN or a fill value.'),
  ('format_consistency',  'format',         'hard', 'Field types, dimensions or units do not match the common data model.'),
  ('global_range',        'range',          'hard', 'Value outside the physically possible range.'),
  ('regional_range',      'range',          'soft', 'Value outside the expected range for the region.'),
  ('spike',               'instrument',     'soft', 'Value departs sharply from its neighbours.'),
  ('position_on_land',    'position',       'hard', 'Latitude/longitude falls on land.'),
  ('position_range',      'position',       'hard', 'Latitude/longitude outside valid limits.'),
  ('timestamp_valid',     'timestamp',      'hard', 'Timestamp unparseable or outside plausible window.'),
  ('timestamp_monotonic', 'timestamp',      'hard', 'Timestamps duplicated or not increasing.'),
  ('density_inversion',   'cross_variable', 'soft', 'Density decreases with depth beyond tolerance.'),
  ('expert_review',       'expert',         'hard', 'Flag set or changed by a human reviewer.');

-- ------------------------------------------------------------ users/roles --

CREATE TABLE app_role (
    role_name   TEXT PRIMARY KEY,
    description TEXT NOT NULL
);
INSERT INTO app_role VALUES
  ('viewer',   'Read data, products and dashboards.'),
  ('reviewer', 'Viewer plus approve/reject datasets, change flags, comment.'),
  ('admin',    'Reviewer plus manage users, ingest and configuration.');

CREATE TABLE app_user (
    user_id       BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    email         TEXT,
    password_hash TEXT,           -- bcrypt via pgcrypto crypt(); POC only
    role_name     TEXT NOT NULL REFERENCES app_role(role_name),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------- dataset catalogue --

CREATE TABLE dataset (
    dataset_id      BIGSERIAL PRIMARY KEY,
    dataset_key     TEXT NOT NULL UNIQUE,             -- stable human key
    title           TEXT NOT NULL,
    dataset_type    TEXT NOT NULL CHECK (dataset_type IN
                      ('profile','timeseries','grid','bathymetry')),
    source          TEXT NOT NULL,                    -- e.g. Argo, NOAA OISST
    instrument      TEXT,
    is_synthetic    BOOLEAN NOT NULL DEFAULT FALSE,   -- POC honesty flag
    license         TEXT,
    time_start      TIMESTAMPTZ,
    time_end        TIMESTAMPTZ,
    bbox            geography(Polygon, 4326),
    zone            TEXT NOT NULL REFERENCES storage_zone(zone),
    storage_uri     TEXT NOT NULL,                    -- s3://bucket/key
    original_format TEXT,                             -- netcdf, csv, ...
    checksum_sha256 TEXT,
    status          TEXT NOT NULL DEFAULT 'ingested' CHECK (status IN
                      ('ingested','qc_running','qc_done','in_review',
                       'approved','rejected','published')),
    conventions     TEXT,                             -- e.g. CF-1.10
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX dataset_bbox_gix ON dataset USING GIST (bbox);
CREATE INDEX dataset_time_ix  ON dataset (time_start, time_end);
CREATE INDEX dataset_status_ix ON dataset (status);

CREATE TABLE dataset_variable (
    dataset_id     BIGINT NOT NULL REFERENCES dataset(dataset_id) ON DELETE CASCADE,
    variable_name  TEXT NOT NULL,       -- common-model standard name
    source_name    TEXT,                -- name in the original file
    units          TEXT NOT NULL,       -- canonical units
    source_units   TEXT,
    long_name      TEXT,
    attributes     JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (dataset_id, variable_name)
);

-- One row per observation = one profile, one time-series record, or one
-- grid time slice. Per-level values live in Zarr, not here.
CREATE TABLE observation (
    observation_id  BIGSERIAL PRIMARY KEY,
    dataset_id      BIGINT NOT NULL REFERENCES dataset(dataset_id) ON DELETE CASCADE,
    platform_id     TEXT,               -- float WMO id, cast id, buoy id
    obs_time        TIMESTAMPTZ,
    geom            geography(Point, 4326),
    min_depth_m     DOUBLE PRECISION,
    max_depth_m     DOUBLE PRECISION,
    n_levels        INTEGER,
    source_file     TEXT,
    source_record   INTEGER,            -- row/profile index in the source file
    overall_flag    SMALLINT REFERENCES qc_flag_scheme(flag) DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX observation_dataset_ix ON observation (dataset_id);
CREATE INDEX observation_time_ix    ON observation (obs_time);
CREATE INDEX observation_geom_gix   ON observation USING GIST (geom);
-- Re-ingesting the same file must not create duplicate observations.
CREATE UNIQUE INDEX observation_source_uq
    ON observation (dataset_id, source_file, source_record)
    WHERE source_file IS NOT NULL AND source_record IS NOT NULL;

-- -------------------------------------------------------------- QC flags --
-- Flags are stored per observation, per variable and (for profiles) per level.
-- level_index = -1 means "whole variable for this observation".
-- A new decision never overwrites an old one: history is kept, and
-- the view qc_flag_current shows the latest decision.

CREATE TABLE qc_flag (
    qc_flag_id      BIGSERIAL PRIMARY KEY,
    observation_id  BIGINT NOT NULL REFERENCES observation(observation_id) ON DELETE CASCADE,
    variable_name   TEXT NOT NULL,
    level_index     INTEGER NOT NULL DEFAULT -1,
    flag            SMALLINT NOT NULL REFERENCES qc_flag_scheme(flag),
    test_id         TEXT NOT NULL REFERENCES qc_test(test_id),
    set_by_user_id  BIGINT REFERENCES app_user(user_id),   -- NULL = automatic
    set_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,     -- value, threshold, ...
    comment         TEXT
);
CREATE INDEX qc_flag_obs_var_ix ON qc_flag (observation_id, variable_name, level_index);
CREATE INDEX qc_flag_test_ix    ON qc_flag (test_id);

CREATE VIEW qc_flag_current AS
SELECT DISTINCT ON (observation_id, variable_name, level_index) *
FROM qc_flag
ORDER BY observation_id, variable_name, level_index, set_at DESC, qc_flag_id DESC;

-- ------------------------------------------------------- review workflow --

CREATE TABLE review (
    review_id     BIGSERIAL PRIMARY KEY,
    dataset_id    BIGINT NOT NULL REFERENCES dataset(dataset_id) ON DELETE CASCADE,
    reviewer_id   BIGINT REFERENCES app_user(user_id),
    decision      TEXT CHECK (decision IN ('approved','rejected','needs_work')),
    comment       TEXT,
    decided_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -------------------------------------------------- immutable audit log --
-- Append-only. UPDATE, DELETE and TRUNCATE are blocked by triggers.
-- Each row stores the hash of the previous row, so any tampering shows up
-- as a broken chain (see audit_verify_chain()).

CREATE TABLE audit_log (
    audit_id     BIGSERIAL PRIMARY KEY,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    user_id      BIGINT,                 -- no FK on purpose: survives user removal
    username     TEXT,
    action       TEXT NOT NULL,          -- e.g. 'flag.change', 'dataset.approve'
    entity_type  TEXT NOT NULL,
    entity_id    TEXT,
    before_state JSONB,
    after_state  JSONB,
    comment      TEXT,
    prev_hash    TEXT,
    row_hash     TEXT
);
CREATE INDEX audit_log_entity_ix ON audit_log (entity_type, entity_id);
CREATE INDEX audit_log_time_ix   ON audit_log (occurred_at);

-- One definition of the row hash, used by the insert trigger and by the
-- verifiers. The timestamp is rendered in UTC with a fixed format so the hash
-- does NOT depend on the session TimeZone of whoever inserts or verifies.
CREATE FUNCTION audit_row_hash(
    p_prev TEXT, p_id BIGINT, p_ts TIMESTAMPTZ, p_user TEXT, p_action TEXT,
    p_etype TEXT, p_eid TEXT, p_before JSONB, p_after JSONB, p_comment TEXT
) RETURNS TEXT AS $$
    SELECT encode(digest(
        p_prev || '|' || p_id::text || '|' ||
        to_char(p_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' ||
        COALESCE(p_user,'') || '|' || p_action || '|' || p_etype || '|' ||
        COALESCE(p_eid,'') || '|' || COALESCE(p_before::text,'') || '|' ||
        COALESCE(p_after::text,'') || '|' || COALESCE(p_comment,''),
        'sha256'), 'hex')
$$ LANGUAGE sql IMMUTABLE;

CREATE FUNCTION audit_log_chain() RETURNS trigger AS $$
DECLARE
    last_hash TEXT;
BEGIN
    -- serialise inserts so the chain order is well defined
    PERFORM pg_advisory_xact_lock(4242);
    SELECT row_hash INTO last_hash FROM audit_log ORDER BY audit_id DESC LIMIT 1;
    NEW.prev_hash := COALESCE(last_hash, 'GENESIS');
    NEW.row_hash  := audit_row_hash(NEW.prev_hash, NEW.audit_id, NEW.occurred_at, NEW.username,
                                    NEW.action, NEW.entity_type, NEW.entity_id,
                                    NEW.before_state, NEW.after_state, NEW.comment);
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_chain_trg
BEFORE INSERT ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_chain();

CREATE FUNCTION audit_log_block_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% not allowed)', TG_OP;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update_delete
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_block_change();

CREATE TRIGGER audit_log_no_truncate
BEFORE TRUNCATE ON audit_log
FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block_change();

-- Verifies the newest p_window rows (cheap, used by /health).
-- Returns the first broken audit_id, or NULL when intact. The oldest row in the
-- window is anchored to the row just before it, so a break at the boundary is
-- still caught.
CREATE FUNCTION audit_verify_recent(p_window INTEGER DEFAULT 1000) RETURNS BIGINT AS $$
DECLARE
    r RECORD;
    expected_prev TEXT;
    prior_hash TEXT;
    first_row BOOLEAN := TRUE;
BEGIN
    FOR r IN SELECT * FROM (SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT p_window) w
             ORDER BY audit_id LOOP
        IF first_row THEN
            SELECT row_hash INTO prior_hash FROM audit_log
             WHERE audit_id < r.audit_id ORDER BY audit_id DESC LIMIT 1;
            expected_prev := COALESCE(prior_hash, 'GENESIS');
            first_row := FALSE;
        END IF;
        IF r.prev_hash IS DISTINCT FROM expected_prev
           OR r.row_hash IS DISTINCT FROM audit_row_hash(
                expected_prev, r.audit_id, r.occurred_at, r.username, r.action, r.entity_type,
                r.entity_id, r.before_state, r.after_state, r.comment) THEN
            RETURN r.audit_id;
        END IF;
        expected_prev := r.row_hash;
    END LOOP;
    RETURN NULL;
END $$ LANGUAGE plpgsql STABLE;

-- Verifies the whole log. Returns the first broken audit_id, or NULL.
CREATE FUNCTION audit_verify_chain() RETURNS BIGINT AS $$
    SELECT audit_verify_recent(2147483647)
$$ LANGUAGE sql STABLE;

-- Development seed users live in 003_dev_seed_users.sql (delete that file for real use).

INSERT INTO audit_log (username, action, entity_type, entity_id, comment)
VALUES ('system', 'schema.init', 'database', 'scats', 'Phase 1 schema created');
