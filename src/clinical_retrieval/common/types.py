"""Shared data structures used across corpus, evalset, retrieval, and eval modules.

Chunk ID convention: ``{chunk_level}::{encounter_id}`` for one-chunk-per-encounter
levels (full_note, structured), ``{chunk_level}::{encounter_id}::{section_name}``
for section, ``{chunk_level}::{encounter_id}::{window_index}`` for fixed_512, and
``{chunk_level}::{patient_id}::{year}`` for patient_summary. Encounter and patient
IDs are the raw FHIR resource ids (UUIDs) from the Synthea export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

ChunkLevel = Literal["full_note", "section", "patient_summary", "fixed_512", "structured"]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    chunk_level: ChunkLevel
    patient_id: str
    text: str
    encounter_id: Optional[str] = None
    encounter_date: Optional[str] = None
    encounter_type: Optional[str] = None
    note_type: tuple[str, ...] = field(default_factory=tuple)
    section_name: Optional[str] = None
    source_resource_ids: tuple[str, ...] = field(default_factory=tuple)
    char_span: Optional[tuple[int, int]] = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["note_type"] = list(self.note_type)
        d["source_resource_ids"] = list(self.source_resource_ids)
        d["char_span"] = list(self.char_span) if self.char_span else None
        return d

    @staticmethod
    def from_dict(d: dict) -> "Chunk":
        d = dict(d)
        d["note_type"] = tuple(d.get("note_type") or ())
        d["source_resource_ids"] = tuple(d.get("source_resource_ids") or ())
        d["char_span"] = tuple(d["char_span"]) if d.get("char_span") else None
        return Chunk(**d)


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float
    rank: int = 0


@dataclass
class EvalQuestion:
    qid: str
    question: str
    tier: int  # 1, 2, 3, or 0 for hand-written
    generation: Literal["programmatic", "hand_written", "paraphrase"]
    answer: str
    gold_chunk_ids: list[str]
    permissive_chunk_ids: list[str]
    gold_resource_ids: list[str]
    patient_id: str
    grounded_in_note: bool
    answerable: bool
    split: Literal["train", "dev", "test"]
    paraphrase_of: Optional[str] = None
    template_id: Optional[str] = None
    question_type: Optional[str] = None  # e.g. "medication_lookup", "allergy_negation"

    # Extensions beyond the spec's illustrative schema (§3.6), needed so the
    # eval harness can recompute gold/permissive chunk ids for chunk levels
    # other than the full_note reference the persisted ids above are pinned
    # to — see evalset/permissive.py.
    gold_encounter_ids: list[str] = field(default_factory=list)
    answer_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(d: dict) -> "EvalQuestion":
        return EvalQuestion(**d)


@dataclass
class Answer:
    question: str
    text: str
    citations: list[str]
    retrieved: list[ScoredChunk]
    timing_ms: dict[str, float]
    raw_context: Optional[str] = None
