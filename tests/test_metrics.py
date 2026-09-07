from clinical_retrieval.common.types import Chunk, ScoredChunk
from clinical_retrieval.eval.metrics import (
    bootstrap_ci,
    mrr_at_k,
    ndcg_at_k,
    paired_bootstrap_test,
    per_question_hits,
    recall_at_k,
)


def _retrieved(ids_in_rank_order):
    return [
        ScoredChunk(chunk=Chunk(chunk_id=cid, chunk_level="full_note", patient_id="p", text=""), score=0.0, rank=r + 1)
        for r, cid in enumerate(ids_in_rank_order)
    ]


def test_recall_at_k_basic():
    retrieved = [_retrieved(["a", "b", "c"]), _retrieved(["x", "y", "z"])]
    relevant = [{"b"}, {"z"}]
    assert recall_at_k(retrieved, relevant, k=3) == 1.0
    assert recall_at_k(retrieved, relevant, k=1) == 0.0  # neither hit is at rank 1


def test_per_question_hits_matches_recall_average():
    retrieved = [_retrieved(["a", "b"]), _retrieved(["x", "y"])]
    relevant = [{"a"}, {"q"}]
    hits = per_question_hits(retrieved, relevant, k=2)
    assert hits == [1.0, 0.0]
    assert recall_at_k(retrieved, relevant, k=2) == sum(hits) / len(hits)


def test_mrr_at_k():
    retrieved = [_retrieved(["a", "b", "c"])]
    relevant = [{"b"}]
    assert mrr_at_k(retrieved, relevant, k=10) == 0.5  # hit at rank 2


def test_ndcg_perfect_ranking_is_one():
    retrieved = [_retrieved(["a", "b"])]
    relevant = [{"a"}]
    assert ndcg_at_k(retrieved, relevant, k=2) == 1.0


def test_bootstrap_ci_mean_matches_sample_mean():
    values = [1.0, 0.0, 1.0, 1.0, 0.0]
    result = bootstrap_ci(values, n_resamples=500, seed=1)
    assert abs(result.mean - 0.6) < 1e-9
    assert result.ci_low <= result.mean <= result.ci_high


def test_paired_bootstrap_test_detects_no_difference():
    values = [1.0, 0.0, 1.0, 0.0, 1.0]
    result = paired_bootstrap_test(values, values, n_resamples=500)
    assert result.diff_mean == 0.0
    assert not result.significant


def test_paired_bootstrap_test_detects_a_real_difference():
    a = [1.0] * 20
    b = [0.0] * 20
    result = paired_bootstrap_test(a, b, n_resamples=500)
    assert result.significant
    assert result.diff_mean == 1.0
