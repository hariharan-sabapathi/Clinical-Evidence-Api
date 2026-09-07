"""Semantic cache: embed the incoming query, cosine-compare against cached
query embeddings, serve the cached answer on similarity >= threshold.

Security property (the subtle bug every semantic cache has to get right):
cache entries are keyed by patient id at the Redis key level --
``semcache:{patient_id}`` -- never by a global key. A clinician scoped to
patient B can therefore never retrieve a cached answer computed for
patient A's chart, because the lookup never even scans patient A's Redis
key; it isn't a matter of the *content* being filtered after the fact, the
*key space* itself is partitioned by patient. See
tests/security/test_semantic_cache_patient_isolation.py.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from app.services.embeddings import cosine_similarity

_MAX_ENTRIES_PER_PATIENT = 50


@dataclass(frozen=True)
class CacheEntry:
    query: str
    answer: str
    citations: list[str]
    embedding: list[float]
    grounded: bool | None = None


class SemanticCache:
    def __init__(self, redis: Redis, similarity_threshold: float, ttl_seconds: int):
        self.redis = redis
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds

    def _key(self, patient_id: str) -> str:
        return f"semcache:{patient_id}"

    async def lookup(self, patient_id: str, query_embedding: list[float]) -> CacheEntry | None:
        # redis-py's method stubs return a Union that includes a non-awaitable
        # branch (to also support pipeline mode) -- cast narrows it back to the
        # actually-awaited async-client shape for the type checker.
        raw_entries = await cast("Awaitable[list[bytes]]", self.redis.lrange(self._key(patient_id), 0, -1))
        best: CacheEntry | None = None
        best_score = -1.0
        for raw in raw_entries:
            data = json.loads(raw)
            entry = CacheEntry(**data)
            score = cosine_similarity(query_embedding, entry.embedding)
            if score >= self.similarity_threshold and score > best_score:
                best, best_score = entry, score
        return best

    async def store(self, patient_id: str, entry: CacheEntry) -> None:
        key = self._key(patient_id)
        payload = json.dumps(
            {
                "query": entry.query,
                "answer": entry.answer,
                "citations": entry.citations,
                "embedding": entry.embedding,
                "grounded": entry.grounded,
            }
        )
        pipe = self.redis.pipeline()
        pipe.lpush(key, payload)
        pipe.ltrim(key, 0, _MAX_ENTRIES_PER_PATIENT - 1)
        pipe.expire(key, self.ttl_seconds)
        await pipe.execute()


class StampedeLock:
    """Short-lived Redis lock so N concurrent identical (or near-identical,
    per the same cache key granularity) queries against a cold cache
    produce exactly one upstream model call instead of N. Losers of the
    race poll the cache for the winner's result rather than calling the
    model themselves."""

    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    def _key(self, patient_id: str, query_hash: str) -> str:
        return f"semcache:lock:{patient_id}:{query_hash}"

    async def acquire(self, patient_id: str, query_hash: str) -> bool:
        return bool(await self.redis.set(self._key(patient_id, query_hash), "1", nx=True, ex=self.ttl_seconds))

    async def release(self, patient_id: str, query_hash: str) -> None:
        await self.redis.delete(self._key(patient_id, query_hash))

    async def wait_for(
        self,
        cache: SemanticCache,
        patient_id: str,
        query_embedding: list[float],
        *,
        timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 0.05,
    ) -> CacheEntry | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            hit = await cache.lookup(patient_id, query_embedding)
            if hit is not None:
                return hit
            await _sleep(poll_interval_seconds)
        return None


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
