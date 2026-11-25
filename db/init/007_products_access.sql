-- Phase 4: derived products and the data access API. ADDITIVE ONLY: nothing in 001-006 is edited.
--
--   * derived_product: one row per product build (lineage, checksums, supersede and stale marks)
--   * catalogue_dataset / catalogue_product: the only things the data access API may read
--   * api_limit + access_set_limit(): limits per role; only an admin token can change them (checked in the database)
--   * query_log: append-only record of every discovery, subset and download
--   * roles scats_products (pipeline) and scats_access (API); neither can see users, sessions, flags or reviews
--
-- DEV PASSWORDS BELOW ARE PUBLISHED IN THE REPOSITORY. Change them before any real use.

-- ------------------------------------------------------------ products --

CREATE TABLE derived_product (
    product_id             BIGSERIAL PRIMARY KEY,
    kind                   TEXT NOT NULL CHECK (kind IN ('binned_profile','series_hourly','series_daily','sst_monthly_mean')),
    product_key            TEXT NOT NULL,                 -- <kind>__<dataset_key>
    version                INTEGER NOT NULL CHECK (version >= 1),
    dataset_id             BIGINT NOT NULL REFERENCES dataset(dataset_id),    -- no cascade: a dataset with products cannot vanish
    dataset_key            TEXT NOT NULL,
    dataset_type           TEXT NOT NULL,
    platform_type          TEXT,
    title                  TEXT NOT NULL,
    description            TEXT NOT NULL,
    source_store_id        BIGINT NOT NULL,
    source_store_uri       TEXT NOT NULL,
    source_manifest_sha256 TEXT NOT NULL,
    source_content_sha256  TEXT NOT NULL,
    source_review_id       BIGINT NOT NULL,
    source_review_version  BIGINT NOT NULL,
    qc_config_version      TEXT,
    uri                    TEXT NOT NULL,                 -- s3://derived/<kind>/<dataset_key>/v<N>.zarr
    zarr_format            SMALLINT NOT NULL,
    content_sha256         TEXT NOT NULL,
    manifest_sha256        TEXT NOT NULL,
    n_objects              INTEGER NOT NULL,
    total_bytes            BIGINT NOT NULL,
    code_version           TEXT NOT NULL,
    code_sha256            TEXT NOT NULL,
    config_sha256          TEXT NOT NULL,
    lineage                JSONB NOT NULL,
    variables              TEXT[] NOT NULL,
    time_start             TIMESTAMPTZ,
    time_end               TIMESTAMPTZ,
    lat_min DOUBLE PRECISION, lat_max DOUBLE PRECISION, lon_min DOUBLE PRECISION, lon_max DOUBLE PRECISION,
    built_by               TEXT NOT NULL,
    built_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at          TIMESTAMPTZ,
    stale_at               TIMESTAMPTZ,
    stale_reason           TEXT,
    UNIQUE (product_key, version)
);
CREATE UNIQUE INDEX derived_product_one_current_ix ON derived_product (product_key) WHERE superseded_at IS NULL;
CREATE INDEX derived_product_dataset_ix ON derived_product (dataset_id);

-- ---------------------------------------------------- stale marking --
-- Runs as the owner, because the roles that change dataset and dataset_store may not touch derived_product.

CREATE FUNCTION product_mark_stale() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    did BIGINT;
    why TEXT;
BEGIN
    IF TG_TABLE_NAME = 'dataset' THEN
        IF OLD.status = 'published' AND NEW.status <> 'published' THEN
            did := NEW.dataset_id;
            why := 'the source dataset left the published state (now ' || NEW.status || ')';
        END IF;
    ELSIF TG_TABLE_NAME = 'dataset_store' THEN
        IF TG_OP = 'DELETE' THEN
            did := OLD.dataset_id;
            why := 'the catalogue row of the source Zarr store was removed';
        ELSIF OLD.manifest_sha256 IS DISTINCT FROM NEW.manifest_sha256 OR OLD.content_sha256 IS DISTINCT FROM NEW.content_sha256
              OR OLD.review_version IS DISTINCT FROM NEW.review_version OR OLD.store_uri IS DISTINCT FROM NEW.store_uri
              OR OLD.review_id IS DISTINCT FROM NEW.review_id THEN
            did := NEW.dataset_id;
            why := 'the source dataset was published again with different content or a different review';
        END IF;
    END IF;
    IF did IS NOT NULL THEN
        UPDATE derived_product SET stale_at = now(), stale_reason = why
         WHERE dataset_id = did AND superseded_at IS NULL AND stale_at IS NULL;
    END IF;
    RETURN COALESCE(NEW, OLD);
END $$;

CREATE TRIGGER dataset_stale_trg AFTER UPDATE OF status ON dataset
    FOR EACH ROW EXECUTE FUNCTION product_mark_stale();
CREATE TRIGGER dataset_store_stale_trg AFTER UPDATE OR DELETE ON dataset_store
    FOR EACH ROW EXECUTE FUNCTION product_mark_stale();

-- ------------------------------------------------------ catalogue views --
-- Plain numbers for the extent: no PostGIS needed (the stand-in test database has none).

CREATE VIEW catalogue_dataset AS
SELECT d.dataset_id, d.dataset_key, d.title, d.dataset_type, d.source, d.instrument, d.is_synthetic, d.license,
       d.attributes->>'platform_type'  AS platform_type,
       d.attributes->>'platform_id'    AS platform_id,
       d.time_start, d.time_end,
       NULLIF(d.attributes->>'geospatial_lat_min','')::DOUBLE PRECISION AS lat_min,
       NULLIF(d.attributes->>'geospatial_lat_max','')::DOUBLE PRECISION AS lat_max,
       NULLIF(d.attributes->>'geospatial_lon_min','')::DOUBLE PRECISION AS lon_min,
       NULLIF(d.attributes->>'geospatial_lon_max','')::DOUBLE PRECISION AS lon_max,
       (SELECT COALESCE(array_agg(v.variable_name ORDER BY v.variable_name), '{}'::TEXT[])
          FROM dataset_variable v WHERE v.dataset_id = d.dataset_id) AS variables,
       s.store_id, s.store_uri, s.zarr_format, s.content_sha256, s.manifest_sha256, s.n_objects, s.total_bytes,
       s.review_id, s.review_version, s.qc_config_version, s.published_at
  FROM dataset d JOIN dataset_store s ON s.dataset_id = d.dataset_id
 WHERE d.status = 'published';

CREATE VIEW catalogue_product AS
SELECT p.product_id, p.kind, p.product_key, p.version, p.dataset_id, p.dataset_key, p.dataset_type, p.platform_type, p.title,
       p.description, p.source_store_uri, p.source_manifest_sha256, p.source_content_sha256, p.source_review_id,
       p.source_review_version, p.qc_config_version, p.uri, p.zarr_format, p.content_sha256, p.manifest_sha256,
       p.n_objects, p.total_bytes, p.code_version, p.code_sha256, p.config_sha256, p.lineage, p.variables,
       p.time_start, p.time_end, p.lat_min, p.lat_max, p.lon_min, p.lon_max, p.built_by, p.built_at, p.superseded_at,
       (p.stale_at IS NOT NULL OR d.dataset_id IS NULL OR d.status <> 'published' OR s.store_id IS NULL
        OR s.manifest_sha256 IS DISTINCT FROM p.source_manifest_sha256
        OR s.review_version  IS DISTINCT FROM p.source_review_version) AS stale,
       CASE WHEN p.stale_at IS NOT NULL THEN p.stale_reason
            WHEN d.status IS DISTINCT FROM 'published' THEN 'the source dataset is not published'
            WHEN s.store_id IS NULL THEN 'the source Zarr store is not catalogued'
            WHEN s.manifest_sha256 IS DISTINCT FROM p.source_manifest_sha256
              OR s.review_version IS DISTINCT FROM p.source_review_version
              THEN 'the source store no longer matches the one the product was built from'
       END AS stale_reason
  FROM derived_product p
  LEFT JOIN dataset d ON d.dataset_id = p.dataset_id
  LEFT JOIN dataset_store s ON s.dataset_id = p.dataset_id;

-- ---------------------------------------------------------- limits --

CREATE TABLE api_limit (
    role_name  TEXT NOT NULL REFERENCES app_role(role_name),
    name       TEXT NOT NULL,
    value      BIGINT NOT NULL CHECK (value > 0),
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_name, name)
);

CREATE FUNCTION api_limit_hard_max(p_name TEXT) RETURNS BIGINT LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_name
        WHEN 'page_size_max'       THEN 1000::BIGINT
        WHEN 'max_rows_subset'     THEN 20000
        WHEN 'max_rows_download'   THEN 5000000
        WHEN 'max_bytes_download'  THEN 1073741824
        WHEN 'max_time_span_days'  THEN 3660
        WHEN 'max_bbox_area_deg2'  THEN 64800
        WHEN 'request_timeout_s'   THEN 300
        WHEN 'rate_per_minute'     THEN 6000
    END
$$;

CREATE FUNCTION api_limit_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF api_limit_hard_max(NEW.name) IS NULL THEN
        RAISE EXCEPTION 'unknown limit %', NEW.name USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.value > api_limit_hard_max(NEW.name) THEN
        RAISE EXCEPTION 'limit % may not exceed %', NEW.name, api_limit_hard_max(NEW.name) USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER api_limit_check_trg BEFORE INSERT OR UPDATE ON api_limit FOR EACH ROW EXECUTE FUNCTION api_limit_check();

INSERT INTO api_limit (role_name, name, value, updated_by)
SELECT r.role_name, l.name, l.value, 'migration 007'
  FROM app_role r,
       (VALUES ('page_size_max', 200), ('max_rows_subset', 5000), ('max_rows_download', 500000),
               ('max_bytes_download', 104857600), ('max_time_span_days', 366), ('max_bbox_area_deg2', 10000),
               ('request_timeout_s', 30), ('rate_per_minute', 60)) AS l(name, value);

-- Only an admin token changes a limit; the check and the audit row are in the same transaction.
CREATE FUNCTION access_set_limit(p_token_hash TEXT, p_role TEXT, p_name TEXT, p_value BIGINT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    a RECORD;
    old BIGINT;
BEGIN
    SELECT * INTO a FROM auth_require_role(p_token_hash, ARRAY['admin']);
    SELECT value INTO old FROM api_limit WHERE role_name = p_role AND name = p_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such limit' USING ERRCODE = 'no_data_found';
    END IF;
    UPDATE api_limit SET value = p_value, updated_by = a.o_username, updated_at = now()
     WHERE role_name = p_role AND name = p_name;
    INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, before_state, after_state, comment)
    VALUES (a.o_user_id, a.o_username, 'limit.change', 'api_limit', p_role || '/' || p_name,
            jsonb_build_object('value', old), jsonb_build_object('value', p_value), 'API limit changed');
    RETURN old;
END $$;

-- ------------------------------------------------------- query log --

CREATE TABLE query_log (
    log_id          BIGSERIAL PRIMARY KEY,
    logged_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    user_id         BIGINT NOT NULL,
    username        TEXT NOT NULL,
    role_name       TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('discover','metadata','zarr_listing','subset','download')),
    target_type     TEXT CHECK (target_type IN ('dataset','product')),
    target_key      TEXT,
    product_version INTEGER,
    data_version    TEXT,                 -- content_sha256 of what was read
    qc_config_sha256 TEXT,
    filter          JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_format   TEXT,
    n_rows          BIGINT,
    n_bytes         BIGINT
);
CREATE INDEX query_log_user_time_ix ON query_log (user_id, logged_at);

CREATE FUNCTION query_log_block_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'query_log is append-only (% not allowed)', TG_OP;
END $$;
CREATE TRIGGER query_log_no_update_delete BEFORE UPDATE OR DELETE ON query_log
    FOR EACH ROW EXECUTE FUNCTION query_log_block_change();
CREATE TRIGGER query_log_no_truncate BEFORE TRUNCATE ON query_log
    FOR EACH STATEMENT EXECUTE FUNCTION query_log_block_change();

-- How many requests has this user made in the last minute? (the API may not read the log itself)
CREATE FUNCTION access_recent_count(p_user BIGINT, p_seconds INTEGER DEFAULT 60)
RETURNS BIGINT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT count(*) FROM query_log WHERE user_id = p_user AND logged_at > clock_timestamp() - make_interval(secs => p_seconds)
$$;
REVOKE ALL ON FUNCTION access_recent_count(BIGINT, INTEGER) FROM PUBLIC;

-- ------------------------------------------------------------ roles --

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'scats_products') THEN
        CREATE ROLE scats_products LOGIN PASSWORD 'scats_products_dev_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'scats_access') THEN
        CREATE ROLE scats_access LOGIN PASSWORD 'scats_access_dev_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO scats_products, scats_access', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO scats_products, scats_access;

-- the pipeline: reads what is published, writes only product rows
GRANT SELECT ON dataset, dataset_store, dataset_variable, storage_zone TO scats_products;
GRANT SELECT, INSERT ON derived_product TO scats_products;
GRANT UPDATE (superseded_at) ON derived_product TO scats_products;
-- audit_log needs SELECT as well as INSERT: the hash-chain trigger (001) reads the previous row as the inserting role
GRANT SELECT, INSERT ON audit_log TO scats_products;
GRANT USAGE ON SEQUENCE derived_product_product_id_seq, audit_log_audit_id_seq TO scats_products;

-- the API: reads two views and the limits; appends to the query log; can call the sign-in functions
GRANT SELECT ON catalogue_dataset, catalogue_product, api_limit TO scats_access;
GRANT INSERT ON query_log TO scats_access;
GRANT SELECT, INSERT ON audit_log TO scats_access;      -- same reason; the API never reads audit_log except the chain check
GRANT USAGE ON SEQUENCE query_log_log_id_seq TO scats_access;

REVOKE ALL ON FUNCTION access_set_limit(TEXT, TEXT, TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION access_set_limit(TEXT, TEXT, TEXT, BIGINT) TO scats_access;
GRANT EXECUTE ON FUNCTION access_recent_count(BIGINT, INTEGER) TO scats_access;
GRANT EXECUTE ON FUNCTION auth_login(TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, INTEGER, INTEGER) TO scats_access;
GRANT EXECUTE ON FUNCTION auth_validate_token(TEXT) TO scats_access;
GRANT EXECUTE ON FUNCTION auth_logout(TEXT) TO scats_access;
GRANT EXECUTE ON FUNCTION auth_default_password_users() TO scats_access;
GRANT EXECUTE ON FUNCTION audit_verify_chain() TO scats_access;
GRANT EXECUTE ON FUNCTION audit_verify_recent(INTEGER) TO scats_access;
