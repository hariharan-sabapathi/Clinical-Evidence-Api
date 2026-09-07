"""Retrieval systems leak across scope boundaries when the vector search
runs before the filter. This test calls the retrieval service directly
(bypassing the API's own authorization check) with a query engineered to
match the *other* patient's text better than the target patient's, and
proves the SQL-level ``WHERE patient_id = :patient_id`` still keeps the
result set from ever including the other patient's rows.
"""

from __future__ import annotations

import pytest

from app.services.retrieval import retrieve
from app.workers.ingest import SERVICE_ACTOR_ID

pytestmark = pytest.mark.integration


async def test_retrieve_only_ever_returns_the_requested_patients_chunks(app_instance, world):
    embedder = app_instance.state.embedder

    async with app_instance.state.db.session_as(SERVICE_ACTOR_ID, "service") as session:
        # "fractured wrist" is patient B's text verbatim, not patient A's --
        # a naive "search everything, filter after" implementation would
        # still surface patient B's chunk if scoring ran unscoped, since it's
        # a near-exact lexical/embedding match. It must not show up here.
        results = await retrieve(session, embedder, str(world.patient_a.id), "fractured wrist", k=5)

    assert len(results) > 0
    assert all(r.patient_id == str(world.patient_a.id) for r in results)
    assert all("wrist" not in r.text.lower() for r in results)
