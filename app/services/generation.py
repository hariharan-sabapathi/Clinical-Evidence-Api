"""Orchestrates one /query call: PHI redaction -> prompt build -> circuit-
breaker-gated model call with a retrieval-only floor on failure ->
grounding check -> re-hydration. Shared by the sync and SSE endpoints so
the fallback/breaker/redaction logic exists in exactly one place.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
import logging
import re
import time
from dataclasses import dataclass

from clinical_retrieval.common.types import Chunk, ScoredChunk
from clinical_retrieval.generation.prompts import build_prompt

from app.core.config import Settings
from app.observability.metrics import llm_cost_usd_total, llm_tokens_total
from app.services.circuit_breaker import CircuitBreaker
from app.services.llm_clients import StreamingLLMClient
from app.services.phi_redaction import PHIRedactor
from app.services.retrieval import RetrievedChunk

logger = logging.getLogger("app.generation")

_CITATION_RE = re.compile(r"\[([^\[\]]+)\]")
# Illustrative per-1K-token pricing so llm_cost_usd_total emits something
# meaningful; swap for the real provider's published rates in production.
_PRICE_PER_1K_PROMPT_USD = 0.005
_PRICE_PER_1K_COMPLETION_USD = 0.015


def _estimate_tokens(text: str) -> int:
    """Rough word-based estimate (~0.75 tokens/word for English) -- good
    enough for the usage metrics without adding a tokenizer dependency
    (tiktoken) just for this."""
    return max(1, int(len(text.split()) * 1.3))


@dataclass
class AnswerResult:
    text: str | None
    citations: list[str]
    grounded: bool | None
    degraded: bool
    served_by: str
    tokens_prompt: int = 0
    tokens_completion: int = 0
    cost_usd: float = 0.0


def _build_retrieved_and_prompt(
    question: str, retrieved: list[RetrievedChunk], redactor: PHIRedactor, known_names: list[str]
) -> tuple[str, list[ScoredChunk], dict[str, str]]:
    scored_chunks: list[ScoredChunk] = []
    merged_mapping: dict[str, str] = {}
    for rc in retrieved:
        redaction = redactor.redact(rc.text, known_names)
        merged_mapping.update(redaction.mapping)
        chunk = Chunk(
            chunk_id=rc.chunk_id,
            chunk_level="fixed_512",
            patient_id=rc.patient_id,
            text=redaction.text,
            encounter_date=rc.encounter_date,
            encounter_type=rc.encounter_type,
            section_name=rc.section_name,
        )
        scored_chunks.append(ScoredChunk(chunk=chunk, score=rc.score))

    question_redaction = redactor.redact(question, known_names)
    merged_mapping.update(question_redaction.mapping)
    prompt = build_prompt(question_redaction.text, scored_chunks)
    return prompt, scored_chunks, merged_mapping


class GenerationService:
    def __init__(
        self,
        settings: Settings,
        primary_client: StreamingLLMClient,
        breaker: CircuitBreaker,
        redactor: PHIRedactor | None = None,
    ):
        self.settings = settings
        self.primary_client = primary_client
        self.breaker = breaker
        self.redactor = redactor or PHIRedactor()

    def _degraded(self) -> AnswerResult:
        return AnswerResult(text=None, citations=[], grounded=None, degraded=True, served_by="retrieval_only")

    def _grounding(self, raw_text: str, valid_chunk_ids: set[str]) -> tuple[list[str], bool]:
        found = [cid for cid in _CITATION_RE.findall(raw_text) if cid in valid_chunk_ids]
        is_refusal = raw_text.strip().lower().startswith("not present in the record")
        grounded = is_refusal or bool(found)
        return found, grounded

    async def answer(
        self, question: str, retrieved: list[RetrievedChunk], actor_id: str, known_names: list[str]
    ) -> AnswerResult:
        # No LLM_MODEL configured -- self.primary_client is NullStreamingClient,
        # which returns a canned refusal that _grounding() above would
        # otherwise read as "grounded", producing a self-contradictory
        # response (an answer body, degraded: false, served_by:
        # retrieval_only) on every request a public instance with no model
        # key serves. Checked first, before the breaker even runs, so the
        # response is unambiguously the same shape the breaker-open path
        # returns.
        if self.primary_client.model_name == "retrieval-only":
            return self._degraded()
        if not self.breaker.allow_request():
            return self._degraded()

        prompt, scored_chunks, mapping = _build_retrieved_and_prompt(question, retrieved, self.redactor, known_names)
        valid_ids = {sc.chunk.chunk_id for sc in scored_chunks}

        try:
            t0 = time.perf_counter()
            raw_text = await asyncio.wait_for(
                asyncio.to_thread(self.primary_client.generate, prompt, self.settings.llm_max_tokens_per_request),
                timeout=self.settings.llm_call_timeout_seconds,
            )
            elapsed = time.perf_counter() - t0
            self.breaker.on_success()
            citations, grounded = self._grounding(raw_text, valid_ids)
            text = self.redactor.rehydrate(raw_text, mapping)
            tokens_prompt = _estimate_tokens(prompt)
            tokens_completion = _estimate_tokens(raw_text)
            self._record_usage(self.primary_client.model_name, tokens_prompt, tokens_completion)
            logger.info(
                "llm_call_succeeded",
                extra={"task": "llm_call", "duration_ms": round(elapsed * 1000, 2)},
            )
            return AnswerResult(
                text=text,
                citations=citations,
                grounded=grounded,
                degraded=False,
                served_by="primary",
                tokens_prompt=tokens_prompt,
                tokens_completion=tokens_completion,
                cost_usd=self._cost(tokens_prompt, tokens_completion),
            )
        except (TimeoutError, Exception) as exc:  # noqa: BLE001
            logger.warning("llm_call_failed", extra={"task": "llm_call"}, exc_info=exc)
            self.breaker.on_failure()
            return self._degraded()

    async def stream_answer(
        self, question: str, retrieved: list[RetrievedChunk], actor_id: str, known_names: list[str]
    ) -> AsyncGenerator[tuple[str, str | AnswerResult], None]:
        """Async generator yielding ('token', str) chunks and finishing with
        exactly one ('done', AnswerResult). The caller (the SSE route) is
        responsible for polling ``request.is_disconnected()`` and closing
        this generator early on disconnect -- ``aclose()`` raises
        GeneratorExit here, which unwinds past the client's ``stream()``
        call and lets it tear down its upstream connection (see
        OpenAICompatibleStreamingClient.stream) instead of continuing to
        pull tokens into a socket nobody is reading anymore."""
        if self.primary_client.model_name == "retrieval-only":
            yield ("done", self._degraded())
            return
        if not self.breaker.allow_request():
            yield ("done", self._degraded())
            return

        prompt, scored_chunks, mapping = _build_retrieved_and_prompt(question, retrieved, self.redactor, known_names)
        valid_ids = {sc.chunk.chunk_id for sc in scored_chunks}

        accumulated = []
        try:
            # contextlib.aclosing, not a bare `async for`: a plain
            # `async for piece in client.stream(...)` does NOT call
            # `.aclose()` on that inner generator when *this* outer
            # generator is itself closed (GeneratorExit lands on the
            # `yield` below, not inside the inner iterator) -- so
            # without this, closing `stream_answer` early would leave
            # the upstream client's stream running unaware anyone
            # stopped listening, which defeats the entire point of
            # cancelling on client disconnect.
            async with contextlib.aclosing(
                self.primary_client.stream(prompt, self.settings.llm_max_tokens_per_request)
            ) as upstream:
                async with asyncio.timeout(self.settings.llm_call_timeout_seconds):
                    async for piece in upstream:
                        accumulated.append(piece)
                        yield ("token", self.redactor.rehydrate(piece, mapping))
            raw_text = "".join(accumulated)
            self.breaker.on_success()
            citations, grounded = self._grounding(raw_text, valid_ids)
            tokens_prompt = _estimate_tokens(prompt)
            tokens_completion = _estimate_tokens(raw_text)
            self._record_usage(self.primary_client.model_name, tokens_prompt, tokens_completion)
            yield (
                "done",
                AnswerResult(
                    text=self.redactor.rehydrate(raw_text, mapping),
                    citations=citations,
                    grounded=grounded,
                    degraded=False,
                    served_by="primary",
                    tokens_prompt=tokens_prompt,
                    tokens_completion=tokens_completion,
                    cost_usd=self._cost(tokens_prompt, tokens_completion),
                ),
            )
            return
        except GeneratorExit:
            logger.info("stream_cancelled_by_client", extra={"task": "llm_call"})
            raise
        except (TimeoutError, Exception) as exc:  # noqa: BLE001
            logger.warning("llm_stream_failed", extra={"task": "llm_call"}, exc_info=exc)
            self.breaker.on_failure()
            yield ("done", self._degraded())

    def _record_usage(self, model: str, tokens_prompt: int, tokens_completion: int) -> None:
        llm_tokens_total.labels(direction="prompt", model=model).inc(tokens_prompt)
        llm_tokens_total.labels(direction="completion", model=model).inc(tokens_completion)
        llm_cost_usd_total.labels(model=model).inc(self._cost(tokens_prompt, tokens_completion))

    @staticmethod
    def _cost(tokens_prompt: int, tokens_completion: int) -> float:
        return (tokens_prompt / 1000) * _PRICE_PER_1K_PROMPT_USD + (
            tokens_completion / 1000
        ) * _PRICE_PER_1K_COMPLETION_USD
