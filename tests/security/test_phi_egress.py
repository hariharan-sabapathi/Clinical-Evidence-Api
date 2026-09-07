"""No HIPAA Safe Harbor identifier should ever appear in the text sent to
the LLM. This is checked by capturing exactly what a scripted client
receives as its prompt -- not by inspecting the final (rehydrated) answer,
which is allowed to contain PHI since that's the clinician-facing side of
the boundary.
"""

from __future__ import annotations

import pytest

from app.services.generation import GenerationService, _build_retrieved_and_prompt
from app.services.phi_redaction import PHIRedactor
from app.services.retrieval import RetrievedChunk

pytestmark = pytest.mark.integration


def test_redactor_strips_all_safe_harbor_identifier_categories():
    redactor = PHIRedactor()
    text = (
        "Patient Jane Marigold Doe (MRN: 004821239), DOB reflected in visit "
        "2019-06-14, was seen at 742 Evergreen Terrace. Contact her at "
        "jane.doe@example.com or (555) 123-4567. SSN on file: 123-45-6789. "
        "She is 94 years old."
    )
    result = redactor.redact(text, known_names=["Jane Marigold Doe"])

    assert "Jane Marigold Doe" not in result.text
    assert "004821239" not in result.text
    assert "2019-06-14" not in result.text
    assert "742 Evergreen Terrace" not in result.text
    assert "jane.doe@example.com" not in result.text
    assert "123-4567" not in result.text
    assert "123-45-6789" not in result.text
    assert "94 years old" not in result.text
    # The year alone is retained -- Safe Harbor permits year-level dates.
    assert "2019" in result.text


def test_prompt_sent_to_llm_contains_no_patient_name_or_precise_date():
    retrieved = [
        RetrievedChunk(
            chunk_id="fixed_512::a::0",
            patient_id="patient-a",
            text="Alice Anderson was seen on 2019-06-14 for management of type 2 diabetes with metformin.",
            score=0.9,
            encounter_date="2019-06-14",
            encounter_type="ambulatory",
            section_name=None,
        )
    ]
    prompt, _, mapping = _build_retrieved_and_prompt(
        "How is Alice Anderson's diabetes managed?", retrieved, PHIRedactor(), known_names=["Alice Anderson"]
    )

    assert "Alice Anderson" not in prompt
    assert "2019-06-14" not in prompt
    assert mapping  # the pseudonym mapping is non-empty and used to re-hydrate the answer later


class _CapturingClient:
    """Scripted LLM client used only to capture exactly what prompt text
    would leave the process boundary -- this is the artifact the test
    inspects, not the final answer."""

    model_name = "capturing-test-double"

    def __init__(self):
        self.captured_prompts: list[str] = []

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        self.captured_prompts.append(prompt)
        return "Not present in the record."


async def test_generation_service_never_sends_phi_to_the_model_call():
    from app.core.config import get_settings
    from app.services.circuit_breaker import CircuitBreaker

    capturing_client = _CapturingClient()
    service = GenerationService(
        settings=get_settings(),
        primary_client=capturing_client,
        breaker=CircuitBreaker(),
    )

    retrieved = [
        RetrievedChunk(
            chunk_id="fixed_512::a::0",
            patient_id="patient-a",
            text="Alice Anderson, DOB 1980-01-01, MRN 55512345, was treated for type 2 diabetes.",
            score=0.9,
            encounter_date="2019-06-14",
            encounter_type="ambulatory",
            section_name=None,
        )
    ]

    await service.answer(
        "What is Alice Anderson being treated for?", retrieved, actor_id="actor-1", known_names=["Alice Anderson"]
    )

    assert len(capturing_client.captured_prompts) == 1
    sent_prompt = capturing_client.captured_prompts[0]
    assert "Alice Anderson" not in sent_prompt
    assert "1980-01-01" not in sent_prompt
    assert "55512345" not in sent_prompt
