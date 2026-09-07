"""Patient metadata pre-filter (§4.2 "also worth testing given this corpus").

Wraps any retriever: if the query names a patient (Synthea gives every
patient a distinctive given name with a numeric suffix, e.g. "Shantelle354",
so a plain token match is enough — no NER model needed), restrict retrieval
to that patient's chunks before scoring. Comparing wrapped vs. unwrapped
isolates how much of the retrieval problem is finding the right patient
versus finding the right encounter within them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from ..common.types import Chunk, ScoredChunk
from .base import Retriever

_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


class PatientNameResolver:
    def __init__(self, patient_names: dict[str, str]):
        self._by_token: dict[str, list[str]] = defaultdict(list)
        for patient_id, name in patient_names.items():
            if not name:
                continue
            first_token = name.split()[0]
            self._by_token[first_token.lower()].append(patient_id)

    def resolve(self, query: str) -> Optional[str]:
        """Returns a patient_id only if exactly one candidate patient's name
        token appears in the query — an ambiguous or absent match falls back
        to unfiltered retrieval rather than guessing."""
        tokens = {t.lower() for t in _TOKEN_RE.findall(query)}
        matches: set[str] = set()
        for token in tokens:
            matches.update(self._by_token.get(token, []))
        if len(matches) == 1:
            return next(iter(matches))
        return None


class PatientFilteredRetriever:
    def __init__(self, base: Retriever, resolver: PatientNameResolver, chunks: list[Chunk]):
        self.base = base
        self.resolver = resolver
        self._chunk_ids_by_patient: dict[str, set[str]] = defaultdict(set)
        for chunk in chunks:
            self._chunk_ids_by_patient[chunk.patient_id].add(chunk.chunk_id)

    def retrieve(
        self, query: str, k: int, allowed_chunk_ids: Optional[set[str]] = None
    ) -> list[ScoredChunk]:
        patient_id = self.resolver.resolve(query)
        if patient_id is None:
            return self.base.retrieve(query, k, allowed_chunk_ids=allowed_chunk_ids)
        patient_chunk_ids = self._chunk_ids_by_patient.get(patient_id, set())
        if allowed_chunk_ids is not None:
            patient_chunk_ids = patient_chunk_ids & allowed_chunk_ids
        return self.base.retrieve(query, k, allowed_chunk_ids=patient_chunk_ids)
