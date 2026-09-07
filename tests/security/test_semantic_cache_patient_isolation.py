"""The subtle bug every semantic cache has: if cache keys aren't
partitioned by patient, a clinician scoped to patient B can retrieve an
answer computed from patient A's chart just by asking a similar-sounding
question. This test proves the partitioning holds at the Redis key level,
not just "in the cases we thought to filter."
"""

from __future__ import annotations

import pytest

from app.services.semantic_cache import CacheEntry, SemanticCache
from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


async def test_cache_entry_for_one_patient_is_invisible_to_another(app_instance, client, world):
    cache: SemanticCache = app_instance.state.semantic_cache
    embedding = app_instance.state.embedder.embed("what medication is the patient on?")

    await cache.store(
        str(world.patient_a.id),
        CacheEntry(
            query="what medication is the patient on?",
            answer="Metformin for type 2 diabetes.",
            citations=["fixed_512::a::0"],
            embedding=embedding,
            grounded=True,
        ),
    )

    # Same embedding, same question text -- the only thing different is
    # which patient's cache partition gets checked.
    hit_for_a = await cache.lookup(str(world.patient_a.id), embedding)
    hit_for_b = await cache.lookup(str(world.patient_b.id), embedding)

    assert hit_for_a is not None
    assert hit_for_a.answer == "Metformin for type 2 diabetes."
    assert hit_for_b is None


async def test_query_endpoint_cache_hit_never_crosses_patients(client, world):
    """End-to-end: clinician_a's query against patient_a populates the
    cache; clinician_b asking the identical question about patient_b must
    still run its own retrieval (and get patient_b's own, different
    answer/evidence), never a cached hit built from patient_a's chart."""
    token_a = await login(client, world.clinician_a.email, world.clinician_a_password)
    token_b = await login(client, world.clinician_b.email, world.clinician_b_password)

    r1 = await client.post(
        f"/v1/patients/{world.patient_a.id}/query", json={"q": "diabetes"}, headers=auth_header(token_a)
    )
    assert r1.status_code == 200

    r2 = await client.post(
        f"/v1/patients/{world.patient_b.id}/query", json={"q": "diabetes"}, headers=auth_header(token_b)
    )
    assert r2.status_code == 200
    assert r2.json()["served_by"] != "cache"
    for citation in r2.json()["citations"]:
        assert citation["patient_id"] == str(world.patient_b.id)
