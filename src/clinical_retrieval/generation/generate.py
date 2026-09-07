"""Turn retrieved chunks + a question into an Answer, logging the raw context
sent on every call (§4.3 — needed for the failure taxonomy in §7)."""

from __future__ import annotations

import re
import time

from ..common.types import Answer, ScoredChunk
from .llm_client import LLMClient
from .prompts import build_prompt

_CITATION_RE = re.compile(r"\[([^\[\]]+)\]")


def generate_answer(question: str, retrieved: list[ScoredChunk], llm_client: LLMClient) -> Answer:
    prompt = build_prompt(question, retrieved)
    t0 = time.perf_counter()
    text = llm_client.generate(prompt)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    valid_ids = {sc.chunk.chunk_id for sc in retrieved}
    citations = [cid for cid in _CITATION_RE.findall(text) if cid in valid_ids]

    return Answer(
        question=question,
        text=text,
        citations=citations,
        retrieved=retrieved,
        timing_ms={"generate": elapsed_ms},
        raw_context=prompt,
    )
