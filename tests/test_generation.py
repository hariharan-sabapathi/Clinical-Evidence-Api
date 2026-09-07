"""Tests for generation/generate.py::generate_answer — the retrieval-evidence
-> LLM -> Answer wiring, using a hand-written fake LLMClient (no network, no
optional dependency needed). This is what actually proves "retrieved
evidence reaches the generation context" and "the answer is grounded in
what was retrieved" at the code level, independent of which concrete
LLMClient implementation is used."""

from clinical_retrieval.common.types import Chunk, ScoredChunk
from clinical_retrieval.generation.generate import generate_answer


def _scored_chunk(chunk_id, patient_id, text, rank=1):
    chunk = Chunk(chunk_id=chunk_id, chunk_level="fixed_512", patient_id=patient_id, text=text)
    return ScoredChunk(chunk=chunk, score=1.0, rank=rank)


class RecordingFakeLLMClient:
    """Captures the exact prompt it was called with, and returns a
    canned response — the smallest possible fake satisfying the LLMClient
    Protocol (just `.generate()`)."""

    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        self.last_prompt = prompt
        return self.response


# --- 4. Retrieved evidence being passed into the generation context --------

def test_retrieved_chunk_text_reaches_the_llm_prompt():
    retrieved = [_scored_chunk("fixed_512::p1::0", "p1", "metformin 500 mg prescribed 2019-05-21")]
    client = RecordingFakeLLMClient("Metformin [fixed_512::p1::0]")

    generate_answer("what medications?", retrieved, client)

    assert "metformin 500 mg prescribed 2019-05-21" in client.last_prompt
    assert "fixed_512::p1::0" in client.last_prompt


def test_multiple_retrieved_chunks_all_reach_the_prompt():
    retrieved = [
        _scored_chunk("c1", "p1", "metformin prescribed 2019-05-21", rank=1),
        _scored_chunk("c2", "p1", "insulin prescribed 2019-01-06", rank=2),
    ]
    client = RecordingFakeLLMClient("answer")
    generate_answer("q", retrieved, client)
    assert "metformin" in client.last_prompt
    assert "insulin" in client.last_prompt


def test_question_reaches_the_prompt():
    client = RecordingFakeLLMClient("answer")
    generate_answer("what medications was Shantelle354 on during her 2019 visits?", [], client)
    assert "what medications was Shantelle354 on during her 2019 visits?" in client.last_prompt


# --- 5. Grounding instructions being included -------------------------------

def test_generation_prompt_carries_the_grounding_rules():
    client = RecordingFakeLLMClient("answer")
    generate_answer("q", [_scored_chunk("c1", "p1", "text")], client)
    assert "Not present in the record." in client.last_prompt
    assert "cite" in client.last_prompt.lower()


# --- Citations: only ids that were actually retrieved get recognized -------

def test_citations_are_extracted_when_they_match_a_retrieved_chunk_id():
    retrieved = [_scored_chunk("fixed_512::p1::0", "p1", "metformin")]
    client = RecordingFakeLLMClient("Metformin [fixed_512::p1::0]")

    answer = generate_answer("q", retrieved, client)

    assert answer.citations == ["fixed_512::p1::0"]
    assert answer.text == "Metformin [fixed_512::p1::0]"


def test_fabricated_citation_ids_are_dropped():
    """If the model cites a chunk id that was never retrieved, that's not a
    real citation — generate_answer must not accept it at face value."""
    retrieved = [_scored_chunk("fixed_512::p1::0", "p1", "metformin")]
    client = RecordingFakeLLMClient("Metformin [fixed_512::p1::999]")  # never retrieved

    answer = generate_answer("q", retrieved, client)

    assert answer.citations == []


def test_answer_carries_the_retrieved_chunks_and_raw_prompt_for_audit():
    retrieved = [_scored_chunk("c1", "p1", "metformin")]
    client = RecordingFakeLLMClient("answer")

    answer = generate_answer("q", retrieved, client)

    assert answer.retrieved == retrieved
    assert answer.raw_context == client.last_prompt


def test_insufficient_evidence_response_carries_no_citations():
    client = RecordingFakeLLMClient("Not present in the record.")
    answer = generate_answer("q", [], client)
    assert answer.citations == []
    assert answer.text == "Not present in the record."
