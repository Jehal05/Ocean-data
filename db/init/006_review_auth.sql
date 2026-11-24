-- Phase 3: accounts, sessions, review role, flag views, Zarr catalogue. ADDITIVE: 001-005 are untouched.
--
-- What this file does
--   * auth_session, login_attempt: tables that NO service role can read or write directly.
--     Everything goes through SECURITY DEFINER functions (auth_*), so a service never sees a password hash,
--     and the admin functions re-check the admin's token inside the database.
--   * scats_review: the role of the review service. It can read the catalogue, ADD human flags and review rows,
--     append to the audit log, and move dataset.status only along the review path (trigger below).
--   * qc_flag_current is redefined: a human decision beats any automatic one (so a QC re-run cannot undo it).
--     qc_flag_auto_current is the newest AUTOMATIC row (what Phase 2 compared against).
--   * dataset_store: where an approved dataset's Zarr lives, its checksums and the review snapshot.
--
-- db/init runs only on a FRESH volume. On an existing database apply this file by hand as the database owner.
-- DEV PASSWORD below. Change it (and DATABASE_URL in docker-compose.yml) for any real use.

-- ------------------------------------------------------------ sessions --

CREATE TABLE auth_session (
    session_id  BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,          -- sha256 of the token; the token itself is never stored
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    ip          TEXT
);
CREATE INDEX auth_session_user_ix ON auth_session (user_id);

CREATE TABLE login_attempt (
    attempt_id   BIGSERIAL PRIMARY KEY,
    username     TEXT NOT NULL,                 -- as typed (cut to 100 characters), never the password
    ip           TEXT,
    success      BOOLEAN NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX login_attempt_user_ix ON login_attempt (username, attempted_at);
CREATE INDEX login_attempt_ip_ix   ON login_attempt (ip, attempted_at);

-- The review service may show who did what, but never password hashes.
CREATE VIEW app_user_public AS
SELECT user_id, username, display_name, role_name, is_active FROM app_user;

-- ------------------------------------------------- review table additions --

ALTER TABLE review
    ADD COLUMN review_version             BIGINT,                          -- max(qc_flag_id) of the dataset at decision time
    ADD COLUMN unreviewed_flag4           INTEGER,                         -- automatic flag-4 cells no person had looked at
    ADD COLUMN accepted_unreviewed_flag4  BOOLEAN NOT NULL DEFAULT FALSE;

-- ---------------------------------------------------------- Zarr catalogue --

CREATE TABLE dataset_store (
    store_id              BIGSERIAL PRIMARY KEY,
    dataset_id            BIGINT NOT NULL UNIQUE REFERENCES dataset(dataset_id) ON DELETE CASCADE,
    review_id             BIGINT NOT NULL REFERENCES review(review_id),
    zone                  TEXT NOT NULL REFERENCES storage_zone(zone),
    store_uri             TEXT NOT NULL,                      -- s3://standardised/<platform_type>/<key>.zarr
    store_format          TEXT NOT NULL DEFAULT 'zarr',
    zarr_format           SMALLINT NOT NULL,
    content_sha256        TEXT NOT NULL,                      -- over the decoded arrays (data and flags)
    manifest_sha256       TEXT NOT NULL,                      -- over the stored objects
    n_objects             INTEGER NOT NULL,
    total_bytes           BIGINT NOT NULL,
    review_version        BIGINT NOT NULL,
    qc_config_version     TEXT,
    published_by          BIGINT REFERENCES app_user(user_id),
    published_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    staging_path          TEXT,
    staging_superseded_at TIMESTAMPTZ                         -- the NetCDF staging file is kept, but superseded
);

-- ------------------------------------------------------------- flag views --

CREATE OR REPLACE VIEW qc_flag_current AS
SELECT DISTINCT ON (observation_id, variable_name, level_index) *
FROM qc_flag
ORDER BY observation_id, variable_name, level_index,
         (set_by_user_id IS NOT NULL) DESC,          -- a person's decision beats any automatic row
         qc_flag_id DESC;                            -- inside each class the newest insert wins (ids, not clocks)

CREATE VIEW qc_flag_auto_current AS
SELECT DISTINCT ON (observation_id, variable_name, level_index) *
FROM qc_flag
WHERE set_by_user_id IS NULL
ORDER BY observation_id, variable_name, level_index, qc_flag_id DESC;

-- Effective flags of one dataset as they were when flag id p_max_id was the newest row (the review snapshot).
CREATE FUNCTION qc_flag_effective_at(p_dataset_id BIGINT, p_max_id BIGINT)
RETURNS TABLE (qc_flag_id BIGINT, observation_id BIGINT, source_record INTEGER, variable_name TEXT,
               level_index INTEGER, flag SMALLINT, test_id TEXT, set_by_user_id BIGINT, comment TEXT) AS $$
    SELECT DISTINCT ON (q.observation_id, q.variable_name, q.level_index)
           q.qc_flag_id, q.observation_id, o.source_record, q.variable_name, q.level_index, q.flag,
           q.test_id, q.set_by_user_id, q.comment
    FROM qc_flag q JOIN observation o ON o.observation_id = q.observation_id
    WHERE o.dataset_id = p_dataset_id AND q.qc_flag_id <= p_max_id
    ORDER BY q.observation_id, q.variable_name, q.level_index, (q.set_by_user_id IS NOT NULL) DESC, q.qc_flag_id DESC
$$ LANGUAGE sql STABLE;

CREATE VIEW dataset_review_counts AS
SELECT d.dataset_id, d.dataset_key, d.status,
       count(*) FILTER (WHERE q.flag = 3)                                       AS n_flag3,
       count(*) FILTER (WHERE q.flag = 4)                                       AS n_flag4,
       count(*) FILTER (WHERE q.flag = 9)                                       AS n_flag9,
       count(*) FILTER (WHERE q.set_by_user_id IS NOT NULL)                     AS n_human,
       count(*) FILTER (WHERE q.flag = 4 AND q.set_by_user_id IS NULL)          AS n_unreviewed_flag4
FROM dataset d
LEFT JOIN observation o ON o.dataset_id = d.dataset_id
LEFT JOIN qc_flag_current q ON q.observation_id = o.observation_id AND q.flag IN (2,3,4,5,8,9)
GROUP BY d.dataset_id, d.dataset_key, d.status;

-- ----------------------------------------------------- database guards --

-- Human flags: only on datasets in review, only values 1-4, always with a person and a reason.
CREATE FUNCTION qc_flag_human_guard() RETURNS trigger AS $$
DECLARE
    st TEXT;
BEGIN
    IF NEW.set_by_user_id IS NOT NULL OR NEW.test_id = 'expert_review' THEN
        IF NEW.set_by_user_id IS NULL OR NEW.test_id <> 'expert_review' THEN
            RAISE EXCEPTION 'a human flag needs set_by_user_id and test_id expert_review' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.comment IS NULL OR length(btrim(NEW.comment)) = 0 THEN
            RAISE EXCEPTION 'a human flag needs a comment' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.flag NOT IN (1, 2, 3, 4) THEN
            RAISE EXCEPTION 'a person may set flags 1-4 only (got %)', NEW.flag USING ERRCODE = 'check_violation';
        END IF;
        SELECT d.status INTO st FROM observation o JOIN dataset d USING (dataset_id) WHERE o.observation_id = NEW.observation_id;
        IF st IS DISTINCT FROM 'in_review' THEN
            RAISE EXCEPTION 'flags can be changed only while the dataset is in_review (status is %)', st
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER qc_flag_human_guard_trg BEFORE INSERT ON qc_flag
FOR EACH ROW EXECUTE FUNCTION qc_flag_human_guard();

-- Status moves for the service roles. Superusers (database maintenance) are not restricted.
CREATE FUNCTION dataset_transition_guard() RETURNS trigger AS $$
DECLARE
    su BOOLEAN;
BEGIN
    SELECT rolsuper INTO su FROM pg_roles WHERE rolname = current_user;
    IF su THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;                                   -- updated_at touch, nothing else
    END IF;
    IF current_user = 'scats_review' THEN
        IF TG_OP = 'UPDATE' AND (OLD.status, NEW.status) IN
           (('qc_done', 'in_review'), ('in_review', 'approved'), ('in_review', 'rejected'), ('approved', 'published')) THEN
            RETURN NEW;
        END IF;
    ELSIF current_user = 'scats_ingest' THEN
        IF TG_OP = 'INSERT' THEN
            RETURN NEW;                               -- 005 limits the first status
        END IF;
        IF OLD.status IN ('ingested', 'qc_running', 'qc_done') AND NEW.status IN ('ingested', 'qc_running', 'qc_done') THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION '% may not move a dataset from % to %', current_user, OLD.status, NEW.status
        USING ERRCODE = 'insufficient_privilege';
END $$ LANGUAGE plpgsql;

CREATE TRIGGER dataset_transition_guard_trg BEFORE INSERT OR UPDATE ON dataset
FOR EACH ROW EXECUTE FUNCTION dataset_transition_guard();

-- ------------------------------------------------------ auth functions --
-- All are SECURITY DEFINER (they run with the owner's rights) with a fixed search_path.
-- Output columns are prefixed o_ so they cannot clash with table columns inside the functions.

CREATE FUNCTION auth_login(
    p_username TEXT, p_password TEXT, p_ip TEXT, p_token_hash TEXT,
    p_ttl_seconds INTEGER, p_max_user INTEGER, p_max_ip INTEGER, p_window_seconds INTEGER
) RETURNS TABLE (o_outcome TEXT, o_user_id BIGINT, o_username TEXT, o_role TEXT, o_expires_at TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    u app_user%ROWTYPE;
    shown TEXT := left(COALESCE(p_username, ''), 100);
    fails_user INTEGER;
    fails_ip INTEGER;
    exp TIMESTAMPTZ;
    ok BOOLEAN := FALSE;
    why TEXT;
BEGIN
    SELECT count(*) INTO fails_user FROM login_attempt a
     WHERE a.username = shown AND NOT a.success AND a.attempted_at > now() - make_interval(secs => p_window_seconds);
    SELECT count(*) INTO fails_ip FROM login_attempt a
     WHERE p_ip IS NOT NULL AND a.ip = p_ip AND NOT a.success AND a.attempted_at > now() - make_interval(secs => p_window_seconds);
    IF fails_user >= p_max_user OR fails_ip >= p_max_ip THEN
        -- blocked attempts are audited but not counted, so the block ends when the window passes
        INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, after_state, comment)
        VALUES (NULL, shown, 'auth.login_blocked', 'user', shown, jsonb_build_object('ip', p_ip), 'too many failed logins');
        RETURN QUERY SELECT 'locked'::TEXT, NULL::BIGINT, NULL::TEXT, NULL::TEXT, NULL::TIMESTAMPTZ;
        RETURN;
    END IF;

    SELECT * INTO u FROM app_user a WHERE a.username = p_username;
    IF NOT FOUND THEN
        PERFORM crypt(COALESCE(p_password, ''), gen_salt('bf', 10));      -- similar work whether or not the user exists
        why := 'unknown user';
    ELSIF u.password_hash IS NULL OR crypt(COALESCE(p_password, ''), u.password_hash) <> u.password_hash THEN
        why := 'wrong password';
    ELSIF NOT u.is_active THEN
        why := 'account disabled';
    ELSE
        ok := TRUE;
    END IF;

    INSERT INTO login_attempt (username, ip, success) VALUES (shown, p_ip, ok);
    IF NOT ok THEN
        INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, after_state, comment)
        VALUES (NULL, shown, 'auth.login_failed', 'user', shown, jsonb_build_object('ip', p_ip, 'reason', why), 'login failed');
        RETURN QUERY SELECT 'denied'::TEXT, NULL::BIGINT, NULL::TEXT, NULL::TEXT, NULL::TIMESTAMPTZ;
        RETURN;
    END IF;

    exp := now() + make_interval(secs => p_ttl_seconds);
    INSERT INTO auth_session (user_id, token_hash, expires_at, ip) VALUES (u.user_id, p_token_hash, exp, p_ip);
    INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, after_state, comment)
    VALUES (u.user_id, u.username, 'auth.login', 'user', u.username,
            jsonb_build_object('ip', p_ip, 'expires_at', exp), 'login');
    RETURN QUERY SELECT 'ok'::TEXT, u.user_id, u.username, u.role_name, exp;
END $$;

CREATE FUNCTION auth_validate_token(p_token_hash TEXT)
RETURNS TABLE (o_user_id BIGINT, o_username TEXT, o_role TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT u.user_id, u.username, u.role_name
      FROM auth_session s JOIN app_user u ON u.user_id = s.user_id
     WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > now() AND u.is_active
$$;

CREATE FUNCTION auth_logout(p_token_hash TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    uid BIGINT;
    uname TEXT;
BEGIN
    UPDATE auth_session SET revoked_at = now() WHERE token_hash = p_token_hash AND revoked_at IS NULL RETURNING user_id INTO uid;
    IF uid IS NULL THEN
        RETURN FALSE;
    END IF;
    SELECT username INTO uname FROM app_user WHERE user_id = uid;
    INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, comment)
    VALUES (uid, uname, 'auth.logout', 'user', uname, 'logout');
    RETURN TRUE;
END $$;

CREATE FUNCTION auth_require_role(p_token_hash TEXT, p_roles TEXT[])
RETURNS TABLE (o_user_id BIGINT, o_username TEXT, o_role TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    r RECORD;
BEGIN
    SELECT * INTO r FROM auth_validate_token(p_token_hash);
    IF NOT FOUND OR NOT (r.o_role = ANY (p_roles)) THEN
        RAISE EXCEPTION 'not authorised' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY SELECT r.o_user_id, r.o_username, r.o_role;
END $$;

CREATE FUNCTION auth_admin_create_user(p_token_hash TEXT, p_username TEXT, p_display TEXT, p_password TEXT, p_role TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    a RECORD;
    new_id BIGINT;
BEGIN
    SELECT * INTO a FROM auth_require_role(p_token_hash, ARRAY['admin']);
    IF p_username IS NULL OR p_username !~ '^[a-z0-9_.-]{3,40}$' THEN
        RAISE EXCEPTION 'username must be 3-40 characters: a-z, 0-9, _ . -' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_password IS NULL OR length(p_password) < 12 THEN
        RAISE EXCEPTION 'password must be at least 12 characters' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app_role WHERE role_name = p_role) THEN
        RAISE EXCEPTION 'unknown role' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO app_user (username, display_name, password_hash, role_name)
    VALUES (p_username, NULLIF(p_display, ''), crypt(p_password, gen_salt('bf', 10)), p_role) RETURNING user_id INTO new_id;
    INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, after_state, comment)
    VALUES (a.o_user_id, a.o_username, 'user.create', 'user', p_username, jsonb_build_object('role', p_role), 'user created');
    RETURN new_id;
END $$;

CREATE FUNCTION auth_admin_set_active(p_token_hash TEXT, p_username TEXT, p_active BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    a RECORD;
    t app_user%ROWTYPE;
BEGIN
    SELECT * INTO a FROM auth_require_role(p_token_hash, ARRAY['admin']);
    SELECT * INTO t FROM app_user WHERE username = p_username;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    IF t.user_id = a.o_user_id THEN
        RAISE EXCEPTION 'you cannot disable or enable your own account' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE app_user SET is_active = p_active WHERE user_id = t.user_id;
    IF NOT p_active THEN
        UPDATE auth_session SET revoked_at = now() WHERE user_id = t.user_id AND revoked_at IS NULL;
    END IF;
    INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, after_state, comment)
    VALUES (a.o_user_id, a.o_username, CASE WHEN p_active THEN 'user.enable' ELSE 'user.disable' END, 'user', p_username,
            jsonb_build_object('is_active', p_active), 'account ' || CASE WHEN p_active THEN 'enabled' ELSE 'disabled' END);
END $$;

CREATE FUNCTION auth_admin_reset_password(p_token_hash TEXT, p_username TEXT, p_new_password TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    a RECORD;
    t app_user%ROWTYPE;
BEGIN
    SELECT * INTO a FROM auth_require_role(p_token_hash, ARRAY['admin']);
    SELECT * INTO t FROM app_user WHERE username = p_username;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    IF p_new_password IS NULL OR length(p_new_password) < 12 THEN
        RAISE EXCEPTION 'password must be at least 12 characters' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE app_user SET password_hash = crypt(p_new_password, gen_salt('bf', 10)) WHERE user_id = t.user_id;
    UPDATE auth_session SET revoked_at = now() WHERE user_id = t.user_id AND revoked_at IS NULL;
    INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, comment)
    VALUES (a.o_user_id, a.o_username, 'user.reset_password', 'user', p_username, 'password reset; sessions revoked');
END $$;

CREATE FUNCTION auth_admin_set_role(p_token_hash TEXT, p_username TEXT, p_role TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    a RECORD;
    t app_user%ROWTYPE;
BEGIN
    SELECT * INTO a FROM auth_require_role(p_token_hash, ARRAY['admin']);
    SELECT * INTO t FROM app_user WHERE username = p_username;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    IF t.user_id = a.o_user_id THEN
        RAISE EXCEPTION 'you cannot change your own role' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app_role WHERE role_name = p_role) THEN
        RAISE EXCEPTION 'unknown role' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE app_user SET role_name = p_role WHERE user_id = t.user_id;
    UPDATE auth_session SET revoked_at = now() WHERE user_id = t.user_id AND revoked_at IS NULL;
    INSERT INTO audit_log (user_id, username, action, entity_type, entity_id, before_state, after_state, comment)
    VALUES (a.o_user_id, a.o_username, 'user.set_role', 'user', p_username, jsonb_build_object('role', t.role_name),
            jsonb_build_object('role', p_role), 'role changed; sessions revoked');
END $$;

-- Deployment check: which development accounts still have their published password?
CREATE FUNCTION auth_default_password_users() RETURNS TEXT[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT COALESCE(array_agg(username ORDER BY username), '{}'::TEXT[])
      FROM app_user
     WHERE is_active AND password_hash IS NOT NULL AND (
           (username = 'admin'    AND password_hash = crypt('admin123',    password_hash)) OR
           (username = 'reviewer' AND password_hash = crypt('reviewer123', password_hash)) OR
           (username = 'viewer'   AND password_hash = crypt('viewer123',   password_hash)))
$$;

-- ----------------------------------------------------------- the role --

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'scats_review') THEN
        CREATE ROLE scats_review LOGIN PASSWORD 'scats_review_dev_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO scats_review', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO scats_review;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO scats_review;
REVOKE ALL ON app_user, auth_session, login_attempt FROM scats_review;      -- only app_user_public and the auth_* functions
GRANT SELECT ON app_user_public TO scats_review;

GRANT INSERT ON qc_flag, review, audit_log, dataset_store TO scats_review;  -- no UPDATE, no DELETE on any of them
GRANT UPDATE (status, updated_at) ON dataset TO scats_review;               -- moves limited by dataset_transition_guard
GRANT UPDATE (staging_superseded_at) ON dataset_store TO scats_review;
GRANT USAGE ON SEQUENCE qc_flag_qc_flag_id_seq, review_review_id_seq, audit_log_audit_id_seq,
                        dataset_store_store_id_seq TO scats_review;

-- Other service roles: read the new views; the ingest service may check tokens (nothing else).
GRANT SELECT ON qc_flag_auto_current, dataset_review_counts TO scats_ingest, scats_app;

REVOKE ALL ON FUNCTION auth_login(TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, INTEGER, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_validate_token(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_logout(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_require_role(TEXT, TEXT[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_admin_create_user(TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_admin_set_active(TEXT, TEXT, BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_admin_reset_password(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_admin_set_role(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_default_password_users() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION auth_login(TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, INTEGER, INTEGER) TO scats_review;
GRANT EXECUTE ON FUNCTION auth_validate_token(TEXT) TO scats_review, scats_ingest;
GRANT EXECUTE ON FUNCTION auth_logout(TEXT) TO scats_review;
GRANT EXECUTE ON FUNCTION auth_require_role(TEXT, TEXT[]) TO scats_review;
GRANT EXECUTE ON FUNCTION auth_admin_create_user(TEXT, TEXT, TEXT, TEXT, TEXT) TO scats_review;
GRANT EXECUTE ON FUNCTION auth_admin_set_active(TEXT, TEXT, BOOLEAN) TO scats_review;
GRANT EXECUTE ON FUNCTION auth_admin_reset_password(TEXT, TEXT, TEXT) TO scats_review;
GRANT EXECUTE ON FUNCTION auth_admin_set_role(TEXT, TEXT, TEXT) TO scats_review;
GRANT EXECUTE ON FUNCTION auth_default_password_users() TO scats_review;
