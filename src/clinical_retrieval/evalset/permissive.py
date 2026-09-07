"""§1.2 cross-encounter answer leakage: compute permissive_chunk_ids on top of
gold_chunk_ids. Recomputed per chunk_level (not just the persisted reference
level) since a chunk's boundaries — and therefore which chunks contain a given
answer term — change with the chunking strategy under test.
"""

from __future__ import annotations

from collections import defaultdict

from ..common.types import Chunk
from .grounding import normalize


def chunks_by_patient(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    by_patient: dict[str, list[Chunk]] = defaultdict(list)
    for c in chunks:
        by_patient[c.patient_id].append(c)
    return dict(by_patient)


def compute_permissive_chunk_ids(
    answer_terms: list[str], patient_id: str, patient_chunks: dict[str, list[Chunk]]
) -> list[str]:
    """Every chunk belonging to this patient whose text contains at least one
    answer term. Scoped to the patient — cross-patient string collisions
    (e.g. a shared medication name) are not leakage in the sense §1.2 means."""
    if not answer_terms:
        return []
    candidates = patient_chunks.get(patient_id, [])
    normalized_terms = [normalize(t) for t in answer_terms]
    matches = []
    for chunk in candidates:
        text = normalize(chunk.text)
        if any(term in text for term in normalized_terms):
            matches.append(chunk.chunk_id)
    return matches


def gold_chunk_ids_for_level(
    gold_encounter_ids: list[str], chunk_level: str, encounter_to_chunk_ids: dict[str, list[str]]
) -> list[str]:
    """Map an eval question's gold encounters to chunk ids for a given chunk
    level, using a precomputed encounter_id -> [chunk_id, ...] lookup (built by
    the caller from that level's chunk list; a section-level encounter maps to
    up to 6 chunks, a fixed_512 encounter may map to 1-2 overlapping windows)."""
    ids: list[str] = []
    for enc_id in gold_encounter_ids:
        ids.extend(encounter_to_chunk_ids.get(enc_id, []))
    return ids


def build_encounter_to_chunk_ids(chunks: list[Chunk]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for c in chunks:
        # source_resource_ids holds contributing encounter ids for fixed_512
        # (a window can span several), or the owning encounter/note id
        # otherwise (see common/types.py + corpus/chunk.py).
        if c.chunk_level == "fixed_512":
            for enc_id in c.source_resource_ids:
                out[enc_id].append(c.chunk_id)
        elif c.encounter_id:
            out[c.encounter_id].append(c.chunk_id)
        else:
            # patient_summary chunks span multiple encounters directly
            for enc_id in c.source_resource_ids:
                out[enc_id].append(c.chunk_id)
    return dict(out)
