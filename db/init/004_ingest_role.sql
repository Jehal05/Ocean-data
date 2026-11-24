-- Phase 2: least-privilege account for the parser/QC ingest service. Additive only.
--
-- Like scats_app, this role is NOT a superuser, owns nothing and cannot disable the audit-log
-- triggers. It can read the catalogue, create and update datasets and observations, ADD QC flags
-- (never change or delete them: flag history is kept) and append to the audit log.
--
-- NOTE: db/init runs only on a FRESH volume. On an existing database run `docker compose down -v`
-- first (this deletes the data), or apply this file by hand as the database owner.
-- DEV PASSWORD below. Change it (and DATABASE_URL_INGEST in docker-compose.yml) for any real use.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'scats_ingest') THEN
        CREATE ROLE scats_ingest LOGIN PASSWORD 'scats_ingest_dev_pw'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO scats_ingest', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO scats_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO scats_ingest;
REVOKE ALL ON app_user FROM scats_ingest;

GRANT INSERT, UPDATE ON dataset, dataset_variable, observation TO scats_ingest;
GRANT INSERT ON qc_flag TO scats_ingest;                     -- no UPDATE, no DELETE
GRANT INSERT ON audit_log TO scats_ingest;
GRANT USAGE ON SEQUENCE dataset_dataset_id_seq, observation_observation_id_seq,
                        qc_flag_qc_flag_id_seq, audit_log_audit_id_seq TO scats_ingest;

-- Flags that arrive from the data provider (Argo *_QC) have no test of ours; give them an origin.
INSERT INTO qc_test VALUES
  ('provider_flag', 'instrument', 'hard', 'Flag supplied by the data provider and mapped to the SCATS scheme.')
ON CONFLICT (test_id) DO NOTHING;
