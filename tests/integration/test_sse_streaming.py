"""SSE streaming: tokens arrive incrementally, a terminal ``done`` event
carries citations and token counts, and a client that disconnects mid
stream causes the generation service's generator to be closed (which is
where a real upstream HTTP call would be torn down) rather than left
running to completion against a socket nobody is reading.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


class _WordStreamingClient:
    """A configured (non-retrieval-only) model that actually streams tokens
    -- the retrieval-only floor (see app/services/generation.py) now skips
    straight to a single ``done`` event with no tokens, so this endpoint
    test needs a client that produces a real streamed answer to prove
    tokens arrive incrementally at all."""

    model_name = "streaming-test-double"

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return "Metformin 500mg [fixed_512::a::0]."

    async def stream(self, prompt: str, max_tokens: int = 512):
        for word in ["Metformin ", "500mg ", "[fixed_512::a::0]."]:
            yield word


async def test_stream_emits_tokens_then_a_done_event(app_instance, client, world):
    from app.services.generation import GenerationService

    app_instance.state.generation_service = GenerationService(
        settings=app_instance.state.settings,
        primary_client=_WordStreamingClient(),
        breaker=app_instance.state.circuit_breaker,
    )

    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    async with client.stream(
        "POST",
        f"/v1/patients/{world.patient_a.id}/query/stream",
        json={"q": "diabetes"},
        headers=auth_header(token),
    ) as response:
        assert response.status_code == 200
        events = []
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())

    assert events[-1] == "done"
    assert events.count("token") >= 1


class _CancellationAwareClient:
    """Never finishes on its own -- proves the SSE endpoint actually closes
    the generator (calling ``aclose()``) rather than letting it run to
    completion when the client disconnects early."""

    model_name = "cancellation-test-double"

    def __init__(self):
        self.cancelled = False

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return "unused"

    async def stream(self, prompt: str, max_tokens: int = 512):
        try:
            for word in ["one ", "two ", "three ", "four ", "five "]:
                yield word
                await asyncio.sleep(0.05)
        except GeneratorExit:
            self.cancelled = True
            raise


async def test_generation_service_stream_propagates_cancellation_to_the_upstream_client(app_instance, world):
    """Exercises the actual mechanism the SSE endpoint relies on: closing
    the ``stream_answer`` async generator early (what the endpoint does
    the moment ``request.is_disconnected()`` returns True, see
    app/api/v1/query.py) must unwind through to the upstream
    ``StreamingLLMClient.stream()`` generator as a ``GeneratorExit``, so a
    real upstream HTTP stream gets torn down instead of continuing to run
    for a client that's gone.

    A true socket-level disconnect is exercised by hand against a live
    server in the README's curl tour; httpx's in-process ASGITransport
    does not reliably synthesize an ASGI ``http.disconnect`` message just
    because the consumer stopped iterating early, so this test targets the
    generator-cancellation contract directly rather than that transport
    detail.
    """
    from app.services.generation import GenerationService
    from app.services.retrieval import RetrievedChunk

    cancellation_client = _CancellationAwareClient()
    service = GenerationService(
        settings=app_instance.state.settings,
        primary_client=cancellation_client,
        breaker=app_instance.state.circuit_breaker,
    )
    retrieved = [
        RetrievedChunk(
            chunk_id="fixed_512::a::0", patient_id=str(world.patient_a.id), text="diabetes note", score=0.5,
            encounter_date=None, encounter_type=None, section_name=None,
        )
    ]

    gen = service.stream_answer("diabetes", retrieved, actor_id=str(world.clinician_a.id), known_names=[])
    kind, first_chunk = await gen.__anext__()
    assert kind == "token"
    await gen.aclose()

    assert cancellation_client.cancelled, "expected GeneratorExit to reach the upstream client's stream()"
