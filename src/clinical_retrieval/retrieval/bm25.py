"""BM25 baseline — the retriever every other variant is measured against
(§4.1). Runs anywhere: `rank_bm25` is pure Python/numpy, no model download."""

from __future__ import annotations

import time
from typing import Optional

from rank_bm25 import BM25Okapi

from ..common.types import Chunk, ScoredChunk
from ..corpus.index import tokenize


class BM25Retriever:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._tokenized = [tokenize(c.text) for c in chunks]
        self._bm25 = BM25Okapi(self._tokenized)

    def retrieve(
        self, query: str, k: int, allowed_chunk_ids: Optional[set[str]] = None
    ) -> list[ScoredChunk]:
        t0 = time.perf_counter()
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        if allowed_chunk_ids is not None:
            ranked = [i for i in ranked if self.chunks[i].chunk_id in allowed_chunk_ids]
        top = ranked[:k]
        result = [ScoredChunk(chunk=self.chunks[i], score=float(scores[i]), rank=r + 1) for r, i in enumerate(top)]
        self.last_timing_ms = {"retrieve": (time.perf_counter() - t0) * 1000}
        return result
