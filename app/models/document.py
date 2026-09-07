from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.db.base import Base

_EMBEDDING_DIM = get_settings().embedding_dim


class Document(Base):
    """One row per retrieval chunk (see src/clinical_retrieval/common/types.py
    for the Chunk shape this mirrors). RLS-scoped: see migration
    0002_row_level_security.py. ``version`` backs optimistic locking on
    PATCH — see app/api/v1/documents.py."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True
    )
    chunk_id: Mapped[str] = mapped_column(String(300), unique=True, nullable=False, index=True)
    chunk_level: Mapped[str] = mapped_column(String(30), nullable=False)
    encounter_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encounter_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    encounter_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    section_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note_type: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    source_resource_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Added in migrations 0004-0006 via the three-step zero-downtime pattern
    # (add nullable -> backfill -> set NOT NULL) documented in ARCHITECTURE.md.
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
