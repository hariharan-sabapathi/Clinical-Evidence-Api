from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    q: str = Field(json_schema_extra={"example": "what medications was the patient on during 2019 visits?"})
    top_k: int | None = Field(default=None, ge=1, le=20)


class Citation(BaseModel):
    chunk_id: str
    patient_id: str
    score: float
    encounter_date: str | None = None
    encounter_type: str | None = None
    section_name: str | None = None


class QueryResponse(BaseModel):
    question: str
    answer: str | None = Field(default=None, json_schema_extra={"example": "Metformin 500mg [chunk_abc]."})
    citations: list[Citation]
    grounded: bool | None = Field(
        description="True if the answer cites at least one retrieved chunk, or explicitly refuses. "
        "A citation-presence check, not claim-level verification -- it does not confirm every "
        "sentence in the answer maps to a chunk."
    )
    degraded: bool = Field(json_schema_extra={"example": False})
    served_by: str = Field(json_schema_extra={"example": "primary"})
    retrieval_ms: float
    generation_ms: float

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "question": "what medications was the patient on during 2019 visits?",
                    "answer": "Metformin 500mg was prescribed during the June 2019 visit [fixed_512::enc123::0].",
                    "citations": [
                        {
                            "chunk_id": "fixed_512::enc123::0",
                            "patient_id": "3f9a6c9e-9c2e-4e77-9c34-6a2f0e9c8b11",
                            "score": 0.87,
                            "encounter_date": "2019-06-14",
                            "encounter_type": "ambulatory",
                            "section_name": None,
                        }
                    ],
                    "grounded": True,
                    "degraded": False,
                    "served_by": "primary",
                    "retrieval_ms": 12.4,
                    "generation_ms": 340.2,
                }
            ]
        }
    }
