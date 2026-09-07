"""20 concurrent, identical queries against a cold semantic cache must
produce exactly one upstream model call -- the stampede lock (a short
Redis SETNX) makes every other concurrent caller wait for the first one's
result instead of independently calling the model.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.generation import GenerationService
from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


class _CountingSlowClient:
    """Sleeps long enough that 20 concurrently dispatched requests are
    guaranteed to overlap in real wall-clock time, and counts how many
    times the model was actually invoked."""

    model_name = "counting-test-double"

    def __init__(self):
        self.call_count = 0
        self._lock = asyncio.Lock()

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        # Runs inside asyncio.to_thread in GenerationService.answer -- a
        # plain time.sleep is correct here, not asyncio.sleep, since this
        # executes in a worker thread, not the event loop.
        import time

        time.sleep(0.2)
        self.call_count += 1
        return "Metformin 500mg [fixed_512::a::0]."


async def test_twenty_concurrent_identical_queries_call_the_model_once(app_instance, client, world):
    counting_client = _CountingSlowClient()
    app_instance.state.generation_service = GenerationService(
        settings=app_instance.state.settings,
        primary_client=counting_client,
        breaker=app_instance.state.circuit_breaker,
    )

    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    url = f"/v1/patients/{world.patient_a.id}/query"
    payload = {"q": "what medication is prescribed for diabetes?"}

    responses = await asyncio.gather(
        *[client.post(url, json=payload, headers=auth_header(token)) for _ in range(20)]
    )

    assert all(r.status_code == 200 for r in responses)
    assert counting_client.call_count == 1, f"expected exactly 1 model call, got {counting_client.call_count}"
