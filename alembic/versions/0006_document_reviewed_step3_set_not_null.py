"""Zero-downtime schema change, step 3 of 3: now that every row has a
non-NULL value (step 2 shipped and ran to completion first), adding the
NOT NULL constraint is a metadata-only change plus a validation scan --
Postgres 16 can validate a NOT NULL against existing data without the
table-rewrite cost a `CREATE TABLE ... NOT NULL` from scratch would need,
as long as no row actually violates it. Deploying this before step 2 has
finished backfilling would fail the constraint outright.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("documents", "reviewed", nullable=False, server_default=sa.text("false"))


def downgrade() -> None:
    op.alter_column("documents", "reviewed", nullable=True, server_default=None)
