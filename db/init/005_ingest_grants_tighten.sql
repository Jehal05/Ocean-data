-- Phase 2 review fix: least privilege for scats_ingest. Additive: 004 is untouched, this file narrows its grants.
--
-- 004 gave scats_ingest table-wide UPDATE on dataset and observation, so the service account could have set
-- dataset.status to 'approved' and skipped the review. After this file it can:
--   * INSERT datasets, but a trigger refuses any status other than ingested / qc_running / qc_done;
--   * UPDATE only the status and updated_at columns of dataset, and only to those three values;
--   * UPDATE only the descriptive columns of observation that a re-ingest rewrites (not dataset_id, source_file, source_record);
--   * INSERT QC flags (never change or delete them), append to the audit log, and read everything except app_user.
-- It has no write access to review, qc_test, qc_flag_scheme, storage_zone, app_role or app_user.
--
-- db/init runs only on a FRESH volume. On an existing database apply this file by hand as the database owner.

REVOKE UPDATE ON dataset, observation, dataset_variable FROM scats_ingest;

GRANT UPDATE (status, updated_at) ON dataset TO scats_ingest;
GRANT UPDATE (platform_id, obs_time, geom, min_depth_m, max_depth_m, n_levels, overall_flag) ON observation TO scats_ingest;
GRANT UPDATE (source_name, units, source_units, long_name, attributes) ON dataset_variable TO scats_ingest;

CREATE OR REPLACE FUNCTION ingest_status_guard() RETURNS trigger AS $$
BEGIN
    IF current_user = 'scats_ingest' AND NEW.status NOT IN ('ingested', 'qc_running', 'qc_done') THEN
        RAISE EXCEPTION 'scats_ingest may not set dataset status to %', NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS dataset_ingest_status_guard ON dataset;
CREATE TRIGGER dataset_ingest_status_guard
BEFORE INSERT OR UPDATE ON dataset
FOR EACH ROW EXECUTE FUNCTION ingest_status_guard();
