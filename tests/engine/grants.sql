-- Proposed engine role (Developer B). Developer A adds the equivalent to the first migration.
-- SELECT on all evidence; INSERT/UPDATE only on the tables the research engine owns.
DO $$ BEGIN
  CREATE ROLE htn_engine LOGIN PASSWORD 'htn_engine';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

GRANT USAGE ON SCHEMA public TO htn_engine;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO htn_engine;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM htn_engine;
GRANT INSERT, UPDATE ON answer_run, tool_event, research_memory TO htn_engine;
