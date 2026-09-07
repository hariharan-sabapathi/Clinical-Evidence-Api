"""§7 failure taxonomy. Retrieval-side codes (R-*, C-SPLIT, Q-AMBIG) can be
assigned automatically from a run's retrieved lists — no LLM needed.
Generation-side codes (G-*) require an actual generated answer to inspect,
which needs an LLM (see README "Environment constraints"); `label_failure`
returns `None` for those rather than guessing, and results/failure_labels.csv
records them as "not applicable — no generation run in this environment".
"""

from __future__ import annotations

from ..common.types import EvalQuestion, ScoredChunk

CODES = {
    "R-MISS": "Gold chunk not in top-k",
    "R-RANK": "Retrieved but ranked below distractors",
    "R-PATIENT": "Retrieved the wrong patient entirely",
    "R-TEMPORAL": "Right patient and fact, wrong encounter",
    "C-SPLIT": "Answer spans a chunk boundary",
    "G-IGNORE": "Correct context retrieved, model answered from elsewhere",
    "G-HALLU": "Fabricated content not in any chunk",
    "G-CITE": "Correct answer, wrong or fabricated citation",
    "G-REFUSE": "Refused despite sufficient context",
    "Q-AMBIG": "Question genuinely ambiguous — eval set defect",
}


def label_retrieval_failure(
    question: EvalQuestion,
    top_k_retrieved: list[ScoredChunk],
    pool_retrieved: list[ScoredChunk],
    gold_ids: set[str],
    permissive_ids: set[str],
) -> str | None:
    """Returns a retrieval-side code, or None if top-k already contains a
    gold chunk (no retrieval failure to label)."""
    if not question.answerable:
        return "Q-AMBIG"

    top_k_ids = {sc.chunk.chunk_id for sc in top_k_retrieved}
    if gold_ids & top_k_ids:
        return None  # success, nothing to label

    # A majority-wrong-patient top-k is the practical failure a user would
    # see, regardless of whether the right patient's chunk exists somewhere
    # deeper in the candidate pool — checked before R-RANK/R-MISS so it isn't
    # masked by "well, it's in the pool of 50 somewhere."
    if top_k_retrieved:
        wrong_patient = sum(1 for sc in top_k_retrieved if sc.chunk.patient_id != question.patient_id)
        if wrong_patient > len(top_k_retrieved) / 2:
            return "R-PATIENT"

    pool_ids = {sc.chunk.chunk_id for sc in pool_retrieved}
    if not (gold_ids & pool_ids) and not (permissive_ids & pool_ids):
        return "R-MISS"

    if permissive_ids & top_k_ids:
        return "R-TEMPORAL"

    if gold_ids & pool_ids:
        return "R-RANK"

    return "R-RANK"
