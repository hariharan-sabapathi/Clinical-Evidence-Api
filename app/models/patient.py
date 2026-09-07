from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Patient(Base):
    """Patients are deliberately NOT row-level-secured on their own table:
    the roster (id, name, DOB) is demographic metadata a clinician needs to
    search for their assigned patients, but the *clinical content* — notes,
    retrieval, audit — is what RLS actually protects. Patients rows returned
    by /v1/patients are still filtered in the query layer to the caller's
    care_assignments; see app/api/v1/patients.py.
    """

    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    given_name: Mapped[str] = mapped_column(String(200), nullable=False)
    family_name: Mapped[str] = mapped_column(String(200), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
