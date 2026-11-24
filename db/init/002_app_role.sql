-- Least-privilege database account for the API.
--
-- The container's POSTGRES_USER is a superuser and owns every table: it can
-- disable the audit-log triggers. The API must NOT connect as that user.
-- This role can read the catalogue and append to the audit log, nothing else.
-- Later phases grant extra write rights table by table, as each component needs them.
--
-- DEV PASSWORD below. Change it (and DATABASE_URL in docker-compose.yml) for any real use.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'scats_app') THEN
        CREATE ROLE scats_app LOGIN PASSWORD 'scats_app_dev_pw'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO scats_app', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO scats_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO scats_app;

-- Accounts and password hashes are not readable by the API (Phase 3 adds a narrow grant).
REVOKE ALL ON app_user FROM scats_app;

-- Append-only: insert allowed, no update/delete (triggers also block these for everyone).
GRANT INSERT ON audit_log TO scats_app;
GRANT USAGE ON SEQUENCE audit_log_audit_id_seq TO scats_app;
