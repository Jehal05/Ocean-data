-- DEVELOPMENT ONLY. Known passwords. Delete this file (or remove the rows)
-- before any real deployment. Accounts are not used by Phase 1 code.
INSERT INTO app_user (username, display_name, password_hash, role_name) VALUES
  ('admin',    'POC Admin',    crypt('admin123',    gen_salt('bf')), 'admin'),
  ('reviewer', 'POC Reviewer', crypt('reviewer123', gen_salt('bf')), 'reviewer'),
  ('viewer',   'POC Viewer',   crypt('viewer123',   gen_salt('bf')), 'viewer');
