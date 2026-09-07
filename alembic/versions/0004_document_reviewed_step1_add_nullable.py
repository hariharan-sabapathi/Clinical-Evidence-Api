"""Zero-downtime schema change, step 1 of 3: add ``documents.reviewed`` as
NULLABLE first. Adding a NOT NULL column directly against a table that
already has rows either fails outright or takes an ACCESS EXCLUSIVE lock
while Postgres rewrites every row to backfill the default — on a table
serving live traffic that's an outage, not a migration. See "Zero-downtime
schema changes" in ARCHITECTURE.md for the full three-step writeup; this
migration is that pattern's step 1, deployed and running before step 2
touches any data.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("reviewed", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "reviewed")
