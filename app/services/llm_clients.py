"""Streaming-capable LLM client wrappers, layered on top of the retrieval
library's existing provider-agnostic ``LLMClient`` protocol
(src/clinical_retrieval/generation/llm_client.py) rather than replacing it
-- the service adds a ``stream()`` method and reuses ``generate()`` as-is.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Protocol

from clinical_retrieval.generation.llm_client import LLMClient, NullLLMClient


class StreamingLLMClient(Protocol):
    model_name: str

    def generate(self, prompt: str, max_tokens: int = 512) -> str: ...

    def stream(self, prompt: str, max_tokens: int = 512) -> AsyncGenerator[str, None]: ...


class NullStreamingClient:
    """Wraps NullLLMClient (used whenever no LLM_MODEL is configured) so the
    SSE endpoint has something real to iterate in dev/CI without a live
    model -- this is also what the disconnect-cancellation test exercises,
    since it doesn't need network access to run."""

    model_name = "retrieval-only"

    def __init__(self) -> None:
        self._inner: LLMClient = NullLLMClient()

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return self._inner.generate(prompt, max_tokens=max_tokens)

    async def stream(self, prompt: str, max_tokens: int = 512) -> AsyncGenerator[str, None]:
        text = self._inner.generate(prompt, max_tokens=max_tokens)
        for word in text.split(" "):
            yield word + " "
            await asyncio.sleep(0)  # yield control so cancellation can interleave


class OpenAICompatibleStreamingClient:
    model_name: str

    def __init__(self, model: str, base_url: str | None = None, api_key_env: str = "LLM_API_KEY") -> None:
        import os

        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"No API key found in ${api_key_env}.")
        from openai import OpenAI  # lazy import

        self.model_name = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        response = self._client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return response.choices[0].message.content or ""

    async def stream(self, prompt: str, max_tokens: int = 512) -> AsyncGenerator[str, None]:
        # The OpenAI SDK's streaming response is a sync context manager;
        # run it in a worker thread and hand deltas back through a queue so
        # this stays a proper async generator the SSE endpoint can cancel.
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _produce() -> None:
            try:
                stream = self._client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.0,
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        loop.call_soon_threadsafe(queue.put_nowait, delta)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = loop.run_in_executor(None, _produce)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            # Best-effort: the executor thread already has an open HTTP
            # stream from the SDK; cancelling the asyncio task here stops
            # this generator from being iterated further so the SSE
            # endpoint stops burning cycles on a client that's gone, even
            # though the underlying thread finishes independently.
            task.cancel()


def build_streaming_client(model: str, base_url: str | None) -> StreamingLLMClient:
    if model == "not-configured":
        return NullStreamingClient()
    try:
        return OpenAICompatibleStreamingClient(model, base_url=base_url)
    except RuntimeError:
        return NullStreamingClient()
