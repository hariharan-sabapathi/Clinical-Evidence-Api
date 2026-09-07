"""§6.5 extra analyses that group an already-computed set of retrieved lists
by some question attribute (tier, generation origin, patient-scope) and
report the same metrics eval/runner.py reports for a whole config, per
group. Kept separate from runner.py because these operate on retrieval
already run once for a fixed config, not on a fresh ablation-grid cell.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..common.types import ScoredChunk
from .metrics import BootstrapResult, bootstrap_ci, mrr_at_k, ndcg_at_k, per_question_hits


@dataclass
class GroupMetrics:
    group: str
    n: int
    recall_lenient_at_5: BootstrapResult
    recall_strict_at_5: BootstrapResult
    mrr_lenient: float
    mrr_strict: float
    ndcg_lenient: float
    ndcg_strict: float


def compute_group_metrics(
    group_name: str,
    indices: list[int],
    all_retrieved: list[list[ScoredChunk]],
    all_gold: list[set[str]],
    all_permissive: list[set[str]],
    k: int = 5,
) -> GroupMetrics:
    retrieved = [all_retrieved[i] for i in indices]
    gold = [all_gold[i] for i in indices]
    permissive = [all_permissive[i] for i in indices]
    return GroupMetrics(
        group=group_name,
        n=len(indices),
        recall_lenient_at_5=bootstrap_ci(per_question_hits(retrieved, permissive, k)),
        recall_strict_at_5=bootstrap_ci(per_question_hits(retrieved, gold, k)),
        mrr_lenient=mrr_at_k(retrieved, permissive, 10),
        mrr_strict=mrr_at_k(retrieved, gold, 10),
        ndcg_lenient=ndcg_at_k(retrieved, permissive, 10),
        ndcg_strict=ndcg_at_k(retrieved, gold, 10),
    )


def group_metrics_to_row(gm: GroupMetrics) -> dict:
    return {
        "group": gm.group,
        "n": gm.n,
        "recall_lenient@5": round(gm.recall_lenient_at_5.mean, 4),
        "recall_lenient@5_ci_lo": round(gm.recall_lenient_at_5.ci_low, 4),
        "recall_lenient@5_ci_hi": round(gm.recall_lenient_at_5.ci_high, 4),
        "recall_strict@5": round(gm.recall_strict_at_5.mean, 4),
        "recall_strict@5_ci_lo": round(gm.recall_strict_at_5.ci_low, 4),
        "recall_strict@5_ci_hi": round(gm.recall_strict_at_5.ci_high, 4),
        "lenient_minus_strict@5": round(gm.recall_lenient_at_5.mean - gm.recall_strict_at_5.mean, 4),
        "mrr_lenient": round(gm.mrr_lenient, 4),
        "mrr_strict": round(gm.mrr_strict, 4),
        "ndcg_lenient": round(gm.ndcg_lenient, 4),
        "ndcg_strict": round(gm.ndcg_strict, 4),
    }
