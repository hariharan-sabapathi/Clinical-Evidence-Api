"""One retrieval interface. `BM25Retriever` is the implementation this
project uses, and `PatientFilteredRetriever` (retrieval/filters.py) wraps it
to restrict retrieval to a named patient's own chunks. Kept as a Protocol
rather than a concrete base class so a different retriever could be
substituted without touching the eval harness or the CLI."""

from __future__ import annotations

from typing import Optional, Protocol

from ..common.types import Chunk, ScoredChunk


class Retriever(Protocol):
    def retrieve(
        self, query: str, k: int, allowed_chunk_ids: Optional[set[str]] = None
    ) -> list[ScoredChunk]: ...


def index_chunks_by_id(chunks: list[Chunk]) -> dict[str, Chunk]:
    return {c.chunk_id: c for c in chunks}
