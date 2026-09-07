import pytest

from clinical_retrieval.eval.metrics import BootstrapResult
from clinical_retrieval.eval.runner import AblationResult, ablation_result_to_row


def test_ablation_row_includes_lenient_minus_strict_gap_for_every_k():
    r = AblationResult(
        label="test", chunk_level="full_note", retriever="bm25", top_k=5,
        patient_filter=False, status="ok", n_questions=10,
    )
    r.recall_lenient = {5: BootstrapResult(0.8, 0.6, 1.0), 10: BootstrapResult(0.9, 0.7, 1.0)}
    r.recall_strict = {5: BootstrapResult(0.5, 0.3, 0.7), 10: BootstrapResult(0.6, 0.4, 0.8)}

    row = ablation_result_to_row(r)

    assert row["lenient_minus_strict@5"] == pytest.approx(0.3)
    assert row["lenient_minus_strict@10"] == pytest.approx(0.3)


def test_gap_is_absent_for_a_skipped_row():
    r = AblationResult(
        label="skipped", chunk_level="full_note", retriever="dense", top_k=5,
        patient_filter=False, status="skipped: no HF access",
    )
    row = ablation_result_to_row(r)
    assert not any(k.startswith("lenient_minus_strict") for k in row)
