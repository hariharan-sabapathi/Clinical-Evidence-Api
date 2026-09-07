"""Tests for generation/prompts.py — the grounding instructions sent to the
LLM (§5 grounding requirements: evidence-only, cite chunk ids, preserve
patient/date context, don't mix patients, distinguish supported claims,
refuse when insufficient)."""

from clinical_retrieval.common.types import Chunk, ScoredChunk
from clinical_retrieval.generation.prompts import build_prompt, format_context


def _scored_chunk(chunk_id, patient_id, text, rank=1):
    chunk = Chunk(chunk_id=chunk_id, chunk_level="fixed_512", patient_id=patient_id, text=text)
    return ScoredChunk(chunk=chunk, score=1.0, rank=rank)


def test_format_context_tags_each_chunk_with_its_id():
    retrieved = [_scored_chunk("fixed_512::p1::0", "p1", "metformin 500mg")]
    context = format_context(retrieved)
    assert "[fixed_512::p1::0]" in context
    assert "metformin 500mg" in context


def test_prompt_includes_the_question_and_all_retrieved_evidence():
    retrieved = [
        _scored_chunk("c1", "p1", "metformin prescribed 2019-05-21"),
        _scored_chunk("c2", "p1", "insulin prescribed 2019-01-06"),
    ]
    prompt = build_prompt("what medications was the patient on?", retrieved)
    assert "what medications was the patient on?" in prompt
    assert "metformin prescribed 2019-05-21" in prompt
    assert "insulin prescribed 2019-01-06" in prompt
    assert "[c1]" in prompt and "[c2]" in prompt


def test_prompt_instructs_evidence_only_answers():
    prompt = build_prompt("q", [_scored_chunk("c1", "p1", "text")])
    assert "only" in prompt.lower()
    assert "invent" in prompt.lower()


def test_prompt_instructs_citation_by_chunk_id():
    prompt = build_prompt("q", [_scored_chunk("c1", "p1", "text")])
    assert "cite" in prompt.lower()
    assert "chunk id" in prompt.lower()


def test_prompt_instructs_preserving_patient_and_date_context():
    prompt = build_prompt("q", [_scored_chunk("c1", "p1", "text")])
    assert "patient" in prompt.lower()
    assert "date" in prompt.lower()


def test_prompt_instructs_not_mixing_different_patients_evidence():
    prompt = build_prompt("q", [_scored_chunk("c1", "p1", "text")])
    assert "different patient" in prompt.lower() or "never combine evidence" in prompt.lower()


def test_prompt_instructs_distinguishing_supported_from_unsupported_claims():
    prompt = build_prompt("q", [_scored_chunk("c1", "p1", "text")])
    assert "distinguish" in prompt.lower()


def test_prompt_instructs_refusal_when_insufficient():
    prompt = build_prompt("q", [_scored_chunk("c1", "p1", "text")])
    assert "Not present in the record." in prompt


def test_prompt_handles_empty_retrieval_without_crashing():
    prompt = build_prompt("q", [])
    assert "q" in prompt
    assert "Not present in the record." in prompt
