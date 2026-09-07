"""Vector retrieval directly against Postgres/pgvector, scoped to one
patient inside the SQL itself -- ``WHERE patient_id = :patient_id ORDER BY
embedding <=> :query_vector`` -- rather than retrieving broadly and
filtering the results in Python afterward.

That distinction is the actual security property under test in
tests/security/test_retrieval_scoping.py: a post-filter still runs the
similarity search over every patient's vectors first, which means (a) it's
wasted work at scale, and worse, (b) a bug that forgets the filter step
leaks results across patients. Putting the predicate in the WHERE clause
means the vector index scan itself never visits another patient's rows, on
top of Postgres RLS enforcing the same boundary a second time
independently (see migration 0002).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.services.embeddings import Embedder


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    patient_id: str
    text: str
    score: float
    encounter_date: str | None
    encounter_type: str | None
    section_name: str | None


async def retrieve(
    session: AsyncSession, embedder: Embedder, patient_id: str, query: str, k: int
) -> list[RetrievedChunk]:
    query_embedding = embedder.embed(query)
    distance = Document.embedding.cosine_distance(query_embedding)
    stmt = (
        select(Document, distance.label("distance"))
        .where(Document.patient_id == patient_id)
        .order_by(distance.asc())
        .limit(k)
    )
    rows = (await session.execute(stmt)).all()
    return [
        RetrievedChunk(
            chunk_id=doc.chunk_id,
            patient_id=str(doc.patient_id),
            text=doc.text,
            score=1.0 - float(dist),  # cosine distance -> similarity
            encounter_date=doc.encounter_date,
            encounter_type=doc.encounter_type,
            section_name=doc.section_name,
        )
        for doc, dist in rows
    ]
