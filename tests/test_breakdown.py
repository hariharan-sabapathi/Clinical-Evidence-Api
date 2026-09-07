from clinical_retrieval.common.types import Chunk, ScoredChunk
from clinical_retrieval.eval.breakdown import compute_group_metrics, group_metrics_to_row


def _retrieved(ids_in_rank_order):
    return [
        ScoredChunk(chunk=Chunk(chunk_id=cid, chunk_level="full_note", patient_id="p", text=""), score=0.0, rank=r + 1)
        for r, cid in enumerate(ids_in_rank_order)
    ]


def test_compute_group_metrics_isolates_the_given_indices():
    all_retrieved = [_retrieved(["a"]), _retrieved(["x"])]
    all_gold = [{"a"}, {"a"}]  # question 1 hits, question 2 misses
    all_permissive = all_gold

    group_hit = compute_group_metrics("hit_group", [0], all_retrieved, all_gold, all_permissive, k=5)
    group_miss = compute_group_metrics("miss_group", [1], all_retrieved, all_gold, all_permissive, k=5)

    assert group_hit.recall_strict_at_5.mean == 1.0
    assert group_miss.recall_strict_at_5.mean == 0.0
    assert group_hit.n == 1


def test_group_metrics_to_row_computes_lenient_minus_strict():
    all_retrieved = [_retrieved(["a"])]
    all_gold = [{"z"}]         # strict miss
    all_permissive = [{"a"}]   # lenient hit

    gm = compute_group_metrics("g", [0], all_retrieved, all_gold, all_permissive, k=5)
    row = group_metrics_to_row(gm)

    assert row["recall_strict@5"] == 0.0
    assert row["recall_lenient@5"] == 1.0
    assert row["lenient_minus_strict@5"] == 1.0


def test_group_metrics_row_has_n_matching_group_size():
    all_retrieved = [_retrieved(["a"]), _retrieved(["b"]), _retrieved(["c"])]
    all_gold = [{"a"}, {"b"}, {"c"}]
    row = group_metrics_to_row(compute_group_metrics("g", [0, 2], all_retrieved, all_gold, all_gold, k=5))
    assert row["n"] == 2
