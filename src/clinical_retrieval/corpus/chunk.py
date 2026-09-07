"""Five chunking strategies over the same corpus, behind one interface, so the
ablation in eval/runner.py can swap `chunk_level` and hold everything else fixed.

`fixed_512` deliberately concatenates each patient's notes into one continuous,
chronologically-ordered stream before windowing — a per-note 512-token window
would rarely split anything (median note ≈ 1,493 chars, well under a 512-token
budget) and would not reproduce the corpus-level chunk count the spec's dataset
facts imply (~2.8M corpus tokens / 512 ≈ 5,470 windows, versus 7,761 notes).
Concatenating first means short adjacent notes get packed into the same window
and long notes get split — the "naive" baseline the spec asks this strategy to
be, complete with windows that straddle encounter boundaries (the source of the
`C-SPLIT` failure mode in the taxonomy).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..common.types import Chunk
from .fhir_serialize import build_encounter_structured_text
from .ndjson_loader import iter_resource, strip_reference
from .notes import SECTION_ORDER, Note, extract_notes

_TOKEN_RE = re.compile(r"\S+")


def load_encounter_meta(raw_dir: str) -> dict[str, dict]:
    meta = {}
    for enc in iter_resource(raw_dir, "Encounter"):
        patient_id = strip_reference(enc.get("subject", {}).get("reference"), "Patient")
        type_text = (enc.get("type") or [{}])[0].get("text")
        meta[enc["id"]] = {
            "patient_id": patient_id,
            "date": (enc.get("period") or {}).get("start"),
            "type": type_text,
        }
    return meta


# ---------------------------------------------------------------------------
# full_note — one chunk per encounter note, the natural unit
# ---------------------------------------------------------------------------


def chunk_full_note(notes: list[Note], encounter_meta: dict) -> list[Chunk]:
    chunks = []
    for note in notes:
        meta = encounter_meta.get(note.encounter_id, {})
        chunks.append(
            Chunk(
                chunk_id=f"full_note::{note.encounter_id}",
                chunk_level="full_note",
                patient_id=note.patient_id,
                text=note.text,
                encounter_id=note.encounter_id,
                encounter_date=meta.get("date"),
                encounter_type=meta.get("type"),
                note_type=note.note_types,
                source_resource_ids=(note.note_id,),
                char_span=(0, len(note.text)),
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# section — one chunk per section header, free structural boundaries
# ---------------------------------------------------------------------------


def chunk_section(notes: list[Note], encounter_meta: dict) -> list[Chunk]:
    chunks = []
    for note in notes:
        meta = encounter_meta.get(note.encounter_id, {})
        for section_name in SECTION_ORDER:
            body = note.sections.get(section_name)
            if not body:
                continue
            start = note.text.find(body)
            span = (start, start + len(body)) if start >= 0 else None
            chunks.append(
                Chunk(
                    chunk_id=f"section::{note.encounter_id}::{section_name}",
                    chunk_level="section",
                    patient_id=note.patient_id,
                    text=body,
                    encounter_id=note.encounter_id,
                    encounter_date=meta.get("date"),
                    encounter_type=meta.get("type"),
                    note_type=note.note_types,
                    section_name=section_name,
                    source_resource_ids=(note.note_id,),
                    char_span=span,
                )
            )
    return chunks


# ---------------------------------------------------------------------------
# fixed_512 — naive fixed-token window over each patient's concatenated notes
# ---------------------------------------------------------------------------


def chunk_fixed_512(notes: list[Note], encounter_meta: dict, window_tokens: int = 512) -> list[Chunk]:
    by_patient: dict[str, list[Note]] = defaultdict(list)
    for note in notes:
        by_patient[note.patient_id].append(note)

    chunks = []
    for patient_id, patient_notes in by_patient.items():
        patient_notes.sort(key=lambda n: n.date or "")

        # Concatenate with a separator, tracking each note's (start, end) span
        # in the concatenated stream so a window can be attributed back to
        # every encounter it overlaps.
        full_text_parts = []
        note_spans: list[tuple[int, int, Note]] = []
        cursor = 0
        for note in patient_notes:
            if full_text_parts:
                full_text_parts.append("\n\n")
                cursor += 2
            start = cursor
            full_text_parts.append(note.text)
            cursor += len(note.text)
            note_spans.append((start, cursor, note))
        full_text = "".join(full_text_parts)

        tokens = list(_TOKEN_RE.finditer(full_text))
        for i in range(0, len(tokens), window_tokens):
            window = tokens[i : i + window_tokens]
            if not window:
                continue
            start_char, end_char = window[0].start(), window[-1].end()
            window_text = full_text[start_char:end_char]

            overlap = Counter()
            for n_start, n_end, note in note_spans:
                overlap_len = max(0, min(end_char, n_end) - max(start_char, n_start))
                if overlap_len > 0:
                    overlap[note.encounter_id] += overlap_len
            if not overlap:
                continue
            primary_encounter_id = overlap.most_common(1)[0][0]
            meta = encounter_meta.get(primary_encounter_id, {})

            chunks.append(
                Chunk(
                    chunk_id=f"fixed_512::{patient_id}::{i // window_tokens}",
                    chunk_level="fixed_512",
                    patient_id=patient_id,
                    text=window_text,
                    encounter_id=primary_encounter_id,
                    encounter_date=meta.get("date"),
                    encounter_type=meta.get("type"),
                    source_resource_ids=tuple(overlap.keys()),
                    char_span=(start_char, end_char),
                )
            )
    return chunks


# ---------------------------------------------------------------------------
# patient_summary — sub-chunked by year, never one blob per patient
# ---------------------------------------------------------------------------


def chunk_patient_summary(notes: list[Note], encounter_meta: dict) -> list[Chunk]:
    """One chunk per (patient, calendar year) with an encounter present, built
    from each encounter's Chief Complaint + Assessment and Plan sections rather
    than the full note. Full notes restate a patient's entire condition history
    on every visit (§1.1); concatenating full notes per year would compound
    that redundancy without adding information the full_note or section
    strategies don't already carry. A 672-encounter patient still gets at most
    one chunk per year they were seen, never one ~1M-character blob."""
    by_patient_year: dict[tuple[str, str], list[Note]] = defaultdict(list)
    for note in notes:
        year = (note.date or "unknown")[:4]
        by_patient_year[(note.patient_id, year)].append(note)

    chunks = []
    for (patient_id, year), year_notes in by_patient_year.items():
        year_notes.sort(key=lambda n: n.date or "")
        parts = []
        encounter_ids = []
        for note in year_notes:
            cc = note.sections.get("Chief Complaint", "")
            ap = note.sections.get("Assessment and Plan", "")
            parts.append(f"[{(note.date or '')[:10]}] Chief Complaint: {cc}\nAssessment and Plan: {ap}")
            encounter_ids.append(note.encounter_id)
        text = "\n\n".join(parts)
        chunks.append(
            Chunk(
                chunk_id=f"patient_summary::{patient_id}::{year}",
                chunk_level="patient_summary",
                patient_id=patient_id,
                text=text,
                encounter_date=f"{year}-01-01" if year != "unknown" else None,
                source_resource_ids=tuple(encounter_ids),
                char_span=(0, len(text)),
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# structured — serialized FHIR resources per encounter (narrative comparison)
# ---------------------------------------------------------------------------


def chunk_structured(raw_dir: str, encounter_meta: dict) -> list[Chunk]:
    structured = build_encounter_structured_text(raw_dir)
    chunks = []
    for encounter_id, text in structured.items():
        meta = encounter_meta.get(encounter_id, {})
        patient_id = meta.get("patient_id")
        if patient_id is None:
            continue
        chunks.append(
            Chunk(
                chunk_id=f"structured::{encounter_id}",
                chunk_level="structured",
                patient_id=patient_id,
                text=text,
                encounter_id=encounter_id,
                encounter_date=meta.get("date"),
                encounter_type=meta.get("type"),
                source_resource_ids=(encounter_id,),
                char_span=(0, len(text)),
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

CHUNK_LEVELS = ("full_note", "section", "patient_summary", "fixed_512", "structured")


def build_chunks(chunk_level: str, raw_dir: str) -> list[Chunk]:
    if chunk_level not in CHUNK_LEVELS:
        raise ValueError(f"Unknown chunk_level {chunk_level!r}, expected one of {CHUNK_LEVELS}")

    encounter_meta = load_encounter_meta(raw_dir)

    if chunk_level == "structured":
        return chunk_structured(raw_dir, encounter_meta)

    notes = extract_notes(raw_dir)
    if chunk_level == "full_note":
        return chunk_full_note(notes, encounter_meta)
    if chunk_level == "section":
        return chunk_section(notes, encounter_meta)
    if chunk_level == "fixed_512":
        return chunk_fixed_512(notes, encounter_meta)
    if chunk_level == "patient_summary":
        return chunk_patient_summary(notes, encounter_meta)
    raise AssertionError("unreachable")
