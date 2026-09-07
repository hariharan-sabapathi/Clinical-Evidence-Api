from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str
    chunk_id: str = Field(json_schema_extra={"example": "fixed_512::b1f2::0"})
    chunk_level: str
    encounter_id: str | None = None
    encounter_date: str | None = None
    encounter_type: str | None = None
    section_name: str | None = None
    text: str
    version: int = Field(json_schema_extra={"example": 1})
    reviewed: bool
    created_at: datetime
    updated_at: datetime


class DocumentPatchRequest(BaseModel):
    text: str | None = Field(default=None, json_schema_extra={"example": "Corrected note text."})
    reviewed: bool | None = Field(default=None, json_schema_extra={"example": True})
