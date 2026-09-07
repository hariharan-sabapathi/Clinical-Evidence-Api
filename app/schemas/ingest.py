from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IngestAcceptedResponse(BaseModel):
    job_id: str = Field(json_schema_extra={"example": "b3f1c2d4-5678-4abc-9def-0123456789ab"})
    status: str = Field(default="queued", json_schema_extra={"example": "queued"})


class IngestJobOut(BaseModel):
    job_id: str
    status: str = Field(json_schema_extra={"example": "running"})
    total_count: int
    processed_count: int
    attempt: int
    error: str | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "job_id": "b3f1c2d4-5678-4abc-9def-0123456789ab",
                    "status": "running",
                    "total_count": 42,
                    "processed_count": 17,
                    "attempt": 1,
                    "error": None,
                }
            ]
        }
    }


class FhirBundleRequest(BaseModel):
    resourceType: str = Field(json_schema_extra={"example": "Bundle"})
    type: str | None = Field(default=None, json_schema_extra={"example": "collection"})
    entry: list[dict[str, Any]] = Field(default_factory=list)
