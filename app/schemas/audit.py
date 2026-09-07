from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: str
    actor_id: str
    actor_role: str
    patient_id: str | None
    resource_type: str
    resource_id: str | None
    action: str
    purpose_of_use: str
    request_id: str
    occurred_at: datetime

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "8f14e45f-ceea-4d1d-8b26-8b7c5b3e4a0f",
                    "actor_id": "2628ea6b-ef24-40d0-929e-9b87236ca05f",
                    "actor_role": "clinician",
                    "patient_id": "3f9a6c9e-9c2e-4e77-9c34-6a2f0e9c8b11",
                    "resource_type": "document_collection",
                    "resource_id": "3f9a6c9e-9c2e-4e77-9c34-6a2f0e9c8b11",
                    "action": "list",
                    "purpose_of_use": "treatment",
                    "request_id": "c108849f-7d35-4f35-9514-ff6e0b65b9e5",
                    "occurred_at": "2026-09-06T19:29:32.358352Z",
                }
            ]
        }
    }
