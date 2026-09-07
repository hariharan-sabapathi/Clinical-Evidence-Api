"""The two reliability acceptance tests called out in the spec: forcing
the circuit breaker open must degrade ``/query`` to retrieval-only (200,
``answer: null``, ``degraded: true``), and the 51st request in a 50/min
window must be rejected with 429 + Retry-After.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


async def test_forced_open_breaker_degrades_query_to_retrieval_only(app_instance, client, world):
    app_instance.state.circuit_breaker.force_open()

    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.post(
        f"/v1/patients/{world.patient_a.id}/query", json={"q": "diabetes"}, headers=auth_header(token)
    )

    assert r.status_code == 200
    body = r.json()
    assert body["answer"] is None
    assert body["degraded"] is True
    assert body["served_by"] == "retrieval_only"
    # The evidence itself is still returned -- that's the whole point of
    # degrading instead of failing outright.
    assert len(body["citations"]) > 0
    assert all(c["patient_id"] == str(world.patient_a.id) for c in body["citations"])


async def test_retrieval_only_config_degrades_without_touching_the_breaker(app_instance, client, world):
    """No LLM_MODEL configured (the default test/public-instance config) --
    the primary client is NullClient, whose canned refusal
    ``_grounding()`` would otherwise read as "grounded". Before this fix
    that produced a self-contradictory response: an answer body and
    ``degraded: false`` alongside ``served_by: retrieval_only``. This never
    forces the breaker open -- it proves the retrieval-only floor is a
    property of the configured client, not of breaker state."""
    assert app_instance.state.generation_service.primary_client.model_name == "retrieval-only"

    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.post(
        f"/v1/patients/{world.patient_a.id}/query", json={"q": "diabetes"}, headers=auth_header(token)
    )

    assert r.status_code == 200
    body = r.json()
    assert body["answer"] is None
    assert body["degraded"] is True
    assert body["served_by"] == "retrieval_only"
    assert len(body["citations"]) > 0


async def test_51st_request_in_one_minute_window_is_rate_limited(app_instance, client, world):
    """End-to-end proof that a burst past the limit gets a 429 with
    Retry-After. The *exact* request index that first gets rejected is
    covered deterministically (with a frozen clock) by
    tests/integration/test_rate_limiter.py -- this test drives real HTTP
    requests, and a real HTTP round trip per request means wall-clock time
    elapses between them, during which the token bucket legitimately
    refills a little. So this asserts the shape of the behavior (every
    request beyond the 50/min budget in a fast burst is eventually
    rejected, with the right headers/body) rather than a specific index.
    """
    from app.services.rate_limiter import TokenBucketRateLimiter

    app_instance.state.rate_limiter = TokenBucketRateLimiter(app_instance.state.redis, 50, 50)

    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    url = f"/v1/patients/{world.patient_a.id}/query"

    responses = []
    for i in range(60):
        r = await client.post(url, json={"q": f"question {i}"}, headers=auth_header(token))
        responses.append(r)

    statuses = [r.status_code for r in responses]
    assert statuses[:50] == [200] * 50, "the first 50 requests are within budget and must all succeed"
    assert 429 in statuses, "at least one request past the 50/min budget must be rejected"

    rejected = next(r for r in responses if r.status_code == 429)
    assert rejected.headers["retry-after"]
    assert rejected.json()["title"] == "Too Many Requests"
