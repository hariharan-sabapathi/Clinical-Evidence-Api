"""audit_events is append-only: revoke UPDATE and DELETE at the database
level so the invariant holds even against an application bug, not just an
application convention. See docs/adr/0005-audit-write-in-read-transaction.md.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM clinical_runtime")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON audit_events TO clinical_runtime")
