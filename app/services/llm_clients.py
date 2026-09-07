"""LLM client wrappers, layered on top of the retrieval library's existing
provider-agnostic ``LLMClient`` protocol
(src/clinical_retrieval/generation/llm_client.py) rather than replacing it.
"""

from __future__ import annotations

from typing import Protocol

from clinical_retrieval.generation.llm_client import LLMClient as _InnerLLMClient
from clinical_retrieval.generation.llm_client import NullLLMClient


class LLMClient(Protocol):
    model_name: str

    def generate(self, prompt: str, max_tokens: int = 512) -> str: ...


class NullClient:
    """Wraps NullLLMClient (used whenever no LLM_MODEL is configured) so the
    service has something real to call in dev/CI without a live model."""

    model_name = "retrieval-only"

    def __init__(self) -> None:
        self._inner: _InnerLLMClient = NullLLMClient()

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return self._inner.generate(prompt, max_tokens=max_tokens)


class OpenAICompatibleClient:
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


def build_llm_client(model: str, base_url: str | None) -> LLMClient:
    if model == "not-configured":
        return NullClient()
    try:
        return OpenAICompatibleClient(model, base_url=base_url)
    except RuntimeError:
        return NullClient()
