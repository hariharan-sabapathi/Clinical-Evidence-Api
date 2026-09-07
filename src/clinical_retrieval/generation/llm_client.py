"""Provider-agnostic LLM client interface for the final answer-generation
stage of the RAG pipeline: retrieved clinical evidence -> LLM -> grounded
answer (see README "Architecture"). Nothing in this repo hardcodes a
specific vendor or model — `pipeline.py` takes an `llm_client` argument that
satisfies this Protocol, and `OpenAICompatibleClient` below works against
any OpenAI-Chat-Completions-compatible endpoint (set `llm_base_url` in
config to point it elsewhere).

No LLM API key is configured in the sandbox this project was built in, so
generation is implemented but unexecuted here — see README "Environment
constraints". Retrieval-only operation (`NullLLMClient`) works without one.
"""

from __future__ import annotations

import os
from typing import Protocol


class LLMClient(Protocol):
    def generate(self, prompt: str, max_tokens: int = 512) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, model: str, base_url: str | None = None, api_key_env: str = "LLM_API_KEY"):
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"No API key found in ${api_key_env}. Set it before constructing "
                "OpenAICompatibleClient — this project never reads it implicitly."
            )
        from openai import OpenAI  # lazy import

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return response.choices[0].message.content or ""


class NullLLMClient:
    """Used when no LLM is configured — pipeline.py degrades to retrieval-only
    output instead of crashing or fabricating an answer."""

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return "Not present in the record. (No LLM configured — retrieval-only mode.)"
