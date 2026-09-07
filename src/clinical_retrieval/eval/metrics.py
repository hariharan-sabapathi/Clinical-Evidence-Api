"""Retrieval metrics, computed both lenient and strict (§1.2, §6.1), with
bootstrap confidence intervals and a paired significance test (§6.4) — at
150-200 questions a 2-point difference is noise, and every table in this repo
says so explicitly rather than implying otherwise.

Recall@k here means "success@k": did at least one of the top-k retrieved
chunks match (lenient: is in permissive_chunk_ids; strict: is in
gold_chunk_ids)? That's what "Recall@k" means in most published RAG-QA
evals with one relevant answer-bearing passage per question — it is not
set recall against the (often huge, per §1.2) permissive set, which would
make lenient Recall@k trivially tiny for any k this benchmark uses.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from ..common.types import ScoredChunk


def _hit_at_k(retrieved: list[ScoredChunk], relevant_ids: set[str], k: int) -> bool:
    return any(sc.chunk.chunk_id in relevant_ids for sc in retrieved[:k])


def recall_at_k(all_retrieved: list[list[ScoredChunk]], all_relevant: list[set[str]], k: int) -> float:
    hits = [_hit_at_k(r, rel, k) for r, rel in zip(all_retrieved, all_relevant)]
    return float(np.mean(hits)) if hits else 0.0


def per_question_hits(all_retrieved: list[list[ScoredChunk]], all_relevant: list[set[str]], k: int) -> list[float]:
    return [1.0 if _hit_at_k(r, rel, k) else 0.0 for r, rel in zip(all_retrieved, all_relevant)]


def _reciprocal_rank(retrieved: list[ScoredChunk], relevant_ids: set[str], cutoff: int) -> float:
    for sc in retrieved[:cutoff]:
        if sc.chunk.chunk_id in relevant_ids:
            return 1.0 / sc.rank
    return 0.0


def mrr_at_k(all_retrieved: list[list[ScoredChunk]], all_relevant: list[set[str]], k: int = 10) -> float:
    values = [_reciprocal_rank(r, rel, k) for r, rel in zip(all_retrieved, all_relevant)]
    return float(np.mean(values)) if values else 0.0


def _dcg(retrieved: list[ScoredChunk], relevant_ids: set[str], k: int) -> float:
    return sum(
        1.0 / math.log2(sc.rank + 1) for sc in retrieved[:k] if sc.chunk.chunk_id in relevant_ids
    )


def ndcg_at_k(all_retrieved: list[list[ScoredChunk]], all_relevant: list[set[str]], k: int = 10) -> float:
    values = []
    for retrieved, relevant in zip(all_retrieved, all_relevant):
        dcg = _dcg(retrieved, relevant, k)
        ideal_hits = min(len(relevant), k)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
        values.append(dcg / idcg if idcg > 0 else 0.0)
    return float(np.mean(values)) if values else 0.0


@dataclass
class BootstrapResult:
    mean: float
    ci_low: float
    ci_high: float


def bootstrap_ci(values: list[float], n_resamples: int = 1000, seed: int = 0) -> BootstrapResult:
    if not values:
        return BootstrapResult(0.0, 0.0, 0.0)
    arr = np.asarray(values)
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    n = len(arr)
    for i in range(n_resamples):
        sample = arr[rng.integers(0, n, size=n)]
        means[i] = sample.mean()
    return BootstrapResult(
        mean=float(arr.mean()),
        ci_low=float(np.percentile(means, 2.5)),
        ci_high=float(np.percentile(means, 97.5)),
    )


@dataclass
class PairedTestResult:
    diff_mean: float
    ci_low: float
    ci_high: float
    significant: bool  # 95% CI on the paired difference excludes 0


def paired_bootstrap_test(
    values_a: list[float], values_b: list[float], n_resamples: int = 1000, seed: int = 0
) -> PairedTestResult:
    """values_a/b: same-length, question-aligned per-question scores for two
    configs sharing a question set (§6.4). Bootstraps the paired difference
    a-b directly, which is the right resampling unit for paired data."""
    if len(values_a) != len(values_b):
        raise ValueError("paired_bootstrap_test requires equal-length, aligned score lists")
    diffs = np.asarray(values_a) - np.asarray(values_b)
    result = bootstrap_ci(list(diffs), n_resamples=n_resamples, seed=seed)
    significant = not (result.ci_low <= 0.0 <= result.ci_high)
    return PairedTestResult(diff_mean=result.mean, ci_low=result.ci_low, ci_high=result.ci_high, significant=significant)
