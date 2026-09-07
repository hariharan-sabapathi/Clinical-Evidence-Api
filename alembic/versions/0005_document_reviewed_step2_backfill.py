"""Zero-downtime schema change, step 2 of 3: backfill existing rows in
batches. A single UPDATE with no WHERE clause takes a lock proportional to
the whole table for the duration of the statement; batching keeps each
transaction short so it doesn't queue up behind (or block) concurrent
reads/writes on a table this size. At the corpus scale this repo ships
with, one batch is already the whole table -- the loop is what matters at
production scale, not the batch count today.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_BATCH_SIZE = 5000


def upgrade() -> None:
    conn = op.get_bind()
    while True:
        result = conn.execute(
            sa.text(
                """
                UPDATE documents SET reviewed = false
                WHERE id IN (
                    SELECT id FROM documents WHERE reviewed IS NULL LIMIT :batch_size
                )
                """
            ),
            {"batch_size": _BATCH_SIZE},
        )
        if result.rowcount == 0:
            break


def downgrade() -> None:
    # Reverting the backfill isn't meaningful on its own -- step 1's downgrade
    # (dropping the column) is what actually undoes this change.
    pass
