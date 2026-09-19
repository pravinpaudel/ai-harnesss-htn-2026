-- Read-only research-engine role (contract: the engine never writes evidence).
-- SELECT on all evidence; INSERT/UPDATE only on the tables the research engine owns.
-- Applied by app.db.bootstrap (htn db init) and by docker-compose on a fresh database volume.
-- Idempotent. Set a real password in production: ALTER ROLE htn_engine PASSWORD '...';
DO $$ BEGIN
  CREATE ROLE htn_engine LOGIN PASSWORD 'htn_engine';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

GRANT USAGE ON SCHEMA public TO htn_engine;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO htn_engine;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM htn_engine;
GRANT INSERT, UPDATE ON answer_run, tool_event, research_memory TO htn_engine;
-- tables created later by the ingest owner are readable, never writable, by the engine
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO htn_engine;
