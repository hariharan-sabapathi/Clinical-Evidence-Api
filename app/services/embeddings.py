"""Embedding backends, kept behind one ``Embedder`` protocol so the vector
column, the semantic cache, and the retrieval query never know which
implementation is behind them -- exactly the pattern the retrieval library
already uses for its LLM client (src/clinical_retrieval/generation/
llm_client.py: ``NullLLMClient`` vs. ``OpenAICompatibleClient``).

``HashingEmbedder`` is the default: a deterministic feature-hashing vector
(bag-of-words hashed into fixed dimensions, L2-normalized) with no model
download and no network call. It's not a state-of-the-art embedding, but it
is a real, non-trivial vector with real cosine-similarity structure --
similar text hashes to similar vectors -- so pgvector's HNSW index and
cosine search, the semantic cache, and every test in this repo exercise
the actual code path a production embedding model would run through. No
LLM API key is configured in this sandbox (see the retrieval library's own
README note on this), so ``OpenAIEmbeddingClient`` is implemented but
unexercised here; set ``LLM_API_KEY`` and wire it in to use it for real.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class OpenAIEmbeddingClient:
    def __init__(self, model: str, dim: int, api_key_env: str = "LLM_API_KEY"):
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"No API key found in ${api_key_env}.")
        from openai import OpenAI  # lazy import, mirrors llm_client.py

        self.model = model
        self.dim = dim
        self._client = OpenAI(api_key=api_key)

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self.model, input=text)
        return [float(value) for value in response.data[0].embedding]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)
