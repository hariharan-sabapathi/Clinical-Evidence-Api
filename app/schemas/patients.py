from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(json_schema_extra={"example": "3f9a6c9e-9c2e-4e77-9c34-6a2f0e9c8b11"})
    external_id: str = Field(json_schema_extra={"example": "Shantelle354"})
    given_name: str = Field(json_schema_extra={"example": "Shantelle354"})
    family_name: str = Field(json_schema_extra={"example": "Rolfson603"})
    birth_date: date | None = Field(default=None, json_schema_extra={"example": "1988-04-12"})
    gender: str | None = Field(default=None, json_schema_extra={"example": "female"})
    created_at: datetime
