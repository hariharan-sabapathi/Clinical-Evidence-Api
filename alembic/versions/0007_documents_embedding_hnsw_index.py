"""Performance optimization (see benchmarks/README.md): an HNSW index on
``documents.embedding`` for cosine distance. Without it, every
``ORDER BY embedding <=> :query_vector LIMIT :k`` is an exact sequential
scan over every one of a patient's chunks -- fine at demo scale, the
dominant cost once a corpus has thousands of chunks per patient. HNSW
trades a small amount of recall for approximate nearest-neighbor search
in roughly logarithmic time.

``m`` and ``ef_construction`` are pgvector's defaults (16 / 64); tuned
lower values would build faster at some recall cost, but the corpus this
ships with is small enough that build time isn't the constraint being
optimized here.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction block; Alembic's
    # autocommit_block() drops out of the migration's normal transaction
    # for this one statement so it can build the index without holding a
    # long-lived lock against writers.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_documents_embedding_hnsw "
            "ON documents USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_documents_embedding_hnsw")
