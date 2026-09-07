"""§4.3 — one prompt template, forced citation, explicit refusal instruction."""

ANSWER_PROMPT_TEMPLATE = """You are answering a question about a patient's clinical record using only \
the numbered excerpts below. Each excerpt is tagged with its chunk id.

{context}

Question: {question}

Rules:
- Answer using only information present in the excerpts above. Do not invent, assume, or infer
  information that is not stated in them.
- Cite every claim with the chunk id(s) it came from, in square brackets, e.g. [{example_id}].
- Preserve the patient's identity and the dates/encounter context exactly as given in the
  excerpts — do not generalize or drop them.
- If the excerpts contain evidence for more than one patient, answer only using the excerpts for
  the patient the question asks about; never combine evidence from different patients.
- Clearly distinguish what the excerpts support from anything you are not certain of — if you are
  not fully supported by the excerpts, say so rather than stating it as fact.
- If the excerpts do not contain enough information to answer, respond exactly:
  "Not present in the record." Do not guess.

Answer:"""


def format_context(retrieved: list) -> str:
    """retrieved: list[ScoredChunk]. Numbered so the model can cite by id."""
    lines = []
    for sc in retrieved:
        lines.append(f"[{sc.chunk.chunk_id}] {sc.chunk.text}")
    return "\n\n".join(lines)


def build_prompt(question: str, retrieved: list) -> str:
    example_id = retrieved[0].chunk.chunk_id if retrieved else "chunk_id"
    return ANSWER_PROMPT_TEMPLATE.format(
        context=format_context(retrieved), question=question, example_id=example_id
    )
