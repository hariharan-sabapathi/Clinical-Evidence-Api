"""grants for the runtime role + row-level security on documents

The API and worker connect as ``clinical_runtime``, never as the schema
owner (``clinical_app``, set as DATABASE_URL_MIGRATOR). A freshly created
role has zero privileges on someone else's tables, so grants come first;
RLS policies are meaningless without the underlying GRANT since Postgres
checks table-level privileges before policy predicates.

Two policies per RLS-protected action:
  * the clinician path: patient_id must be in the caller's active
    care_assignments, looked up by ``current_setting('app.actor_id')``.
  * the service path: the ingestion worker writes documents for any
    patient, not just one clinician's — it authenticates as the same DB
    role but sets ``app.actor_role = 'service'`` for the duration of its
    write transaction (see app/workers/ingest.py).

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_TABLES_RUNTIME_CRUD_NO_DELETE = ["documents", "audit_events", "ingest_jobs"]
_TABLES_RUNTIME_FULL = ["users", "refresh_tokens", "patients", "care_assignments", "idempotency_keys"]


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO clinical_runtime")

    for table in _TABLES_RUNTIME_FULL:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO clinical_runtime")
    for table in _TABLES_RUNTIME_CRUD_NO_DELETE:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO clinical_runtime")

    # --- Row Level Security on documents ---
    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    # FORCE means even the table owner is subject to RLS. Belt-and-suspenders:
    # the owner (clinical_app) never runs application queries anyway (see
    # config.py), but this stops a future migration from accidentally
    # widening access by connecting as the owner.
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY documents_select_scoped ON documents
          FOR SELECT
          USING (
            current_setting('app.actor_role', true) = 'service'
            OR patient_id IN (
              SELECT patient_id FROM care_assignments
              WHERE clinician_id = current_setting('app.actor_id', true)::uuid
                AND revoked_at IS NULL
            )
          )
        """
    )
    op.execute(
        """
        CREATE POLICY documents_modify_scoped ON documents
          FOR UPDATE
          USING (
            current_setting('app.actor_role', true) = 'service'
            OR patient_id IN (
              SELECT patient_id FROM care_assignments
              WHERE clinician_id = current_setting('app.actor_id', true)::uuid
                AND revoked_at IS NULL
            )
          )
          WITH CHECK (
            current_setting('app.actor_role', true) = 'service'
            OR patient_id IN (
              SELECT patient_id FROM care_assignments
              WHERE clinician_id = current_setting('app.actor_id', true)::uuid
                AND revoked_at IS NULL
            )
          )
        """
    )
    op.execute(
        """
        CREATE POLICY documents_insert_service_only ON documents
          FOR INSERT
          WITH CHECK (current_setting('app.actor_role', true) = 'service')
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS documents_insert_service_only ON documents")
    op.execute("DROP POLICY IF EXISTS documents_modify_scoped ON documents")
    op.execute("DROP POLICY IF EXISTS documents_select_scoped ON documents")
    op.execute("ALTER TABLE documents NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents DISABLE ROW LEVEL SECURITY")

    for table in _TABLES_RUNTIME_CRUD_NO_DELETE:
        op.execute(f"REVOKE SELECT, INSERT, UPDATE ON {table} FROM clinical_runtime")
    for table in _TABLES_RUNTIME_FULL:
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM clinical_runtime")
    op.execute("REVOKE USAGE ON SCHEMA public FROM clinical_runtime")
