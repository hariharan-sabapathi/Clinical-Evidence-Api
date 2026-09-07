-- Runs once, automatically, when the postgres container's data directory
-- is first initialized (standard pgvector/pgvector:pg16 entrypoint
-- behavior for anything mounted at /docker-entrypoint-initdb.d/).
--
-- Creates the two-role split migration 0002 depends on: `clinical_app`
-- owns the schema and runs migrations; `clinical_runtime` is the
-- RLS-restricted role the API and worker actually connect as. See
-- app/core/config.py and docs/adr/0001-postgres-rls-over-application-authz.md.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE ROLE clinical_app LOGIN PASSWORD 'devpassword' CREATEDB;
CREATE ROLE clinical_runtime LOGIN PASSWORD 'runtimepassword'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

GRANT CONNECT ON DATABASE clinical_evidence TO clinical_runtime;
ALTER DATABASE clinical_evidence OWNER TO clinical_app;
