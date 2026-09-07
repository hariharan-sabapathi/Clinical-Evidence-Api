"""Config-driven ablation runner (§6.2). Ablates one axis at a time from a
fixed reference config (configs/reference.yml) rather than the full
cross-product — and says so, per the spec's explicit instruction.

BM25 is the only retriever in this project's final scope (a clinical RAG
pipeline over BM25 retrieval — see README "Architecture"); dense/hybrid/
reranking/query-rewriting were explored earlier and removed rather than
kept around unexecuted. `build_retriever` therefore has exactly one real
path plus the patient metadata filter, which wraps it.
"""

from __future__ import annotations

import csv as csv_module
import statistics
import time
from dataclasses import dataclass, field

from ..common.types import Chunk, EvalQuestion, ScoredChunk
from ..corpus.chunk import build_chunks
from ..evalset.permissive import build_encounter_to_chunk_ids, chunks_by_patient, compute_permissive_chunk_ids
from .metrics import BootstrapResult, bootstrap_ci, mrr_at_k, ndcg_at_k, per_question_hits


def build_retriever(name: str, chunks: list[Chunk], config, patient_names: dict[str, str] | None = None):
    from ..retrieval.bm25 import BM25Retriever

    if name != "bm25":
        raise ValueError(f"Unknown retriever {name!r} — this project only implements bm25")
    base = BM25Retriever(chunks)

    if config.retrieval.patient_filter:
        if not patient_names:
            raise ValueError("patient_filter requires patient_names")
        from ..retrieval.filters import PatientFilteredRetriever, PatientNameResolver

        base = PatientFilteredRetriever(base, PatientNameResolver(patient_names), chunks)
    return base


@dataclass
class AblationResult:
    label: str
    chunk_level: str
    retriever: str
    top_k: int
    patient_filter: bool
    status: str  # "ok" | "skipped: <reason>"
    n_questions: int = 0
    recall_lenient: dict[int, BootstrapResult] = field(default_factory=dict)
    recall_strict: dict[int, BootstrapResult] = field(default_factory=dict)
    mrr_lenient: float = 0.0
    mrr_strict: float = 0.0
    ndcg_lenient: float = 0.0
    ndcg_strict: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    per_question_hit_strict: dict[str, float] = field(default_factory=dict)
    per_question_hit_lenient: dict[str, float] = field(default_factory=dict)


K_VALUES = (1, 5, 10, 20)


def run_config(
    questions: list[EvalQuestion],
    chunk_level: str,
    retriever_name: str,
    top_k: int,
    config,
    raw_dir: str = "data/raw",
    split: str = "test",
    patient_names: dict[str, str] | None = None,
    chunks: list[Chunk] | None = None,
    label: str | None = None,
) -> AblationResult:
    label = label or f"{chunk_level}|{retriever_name}|k={top_k}|patient_filter={config.retrieval.patient_filter}"
    eval_qs = [q for q in questions if q.split == split and q.answerable]
    if not eval_qs:
        return AblationResult(label, chunk_level, retriever_name, top_k, config.retrieval.patient_filter, "skipped: no eval questions")

    if chunks is None:
        chunks = build_chunks(chunk_level, raw_dir)
    patient_chunks = chunks_by_patient(chunks)
    encounter_to_chunk = build_encounter_to_chunk_ids(chunks)

    retriever = build_retriever(retriever_name, chunks, config, patient_names)

    max_k = max(K_VALUES + (top_k,))
    all_retrieved: list[list[ScoredChunk]] = []
    all_gold: list[set[str]] = []
    all_permissive: list[set[str]] = []
    latencies_ms = []

    for q in eval_qs:
        t0 = time.perf_counter()
        retrieved = retriever.retrieve(q.question, max_k)
        latencies_ms.append((time.perf_counter() - t0) * 1000)

        gold_ids = set()
        for enc_id in q.gold_encounter_ids:
            gold_ids.update(encounter_to_chunk.get(enc_id, []))
        permissive_ids = set(gold_ids) | set(
            compute_permissive_chunk_ids(q.answer_terms, q.patient_id, patient_chunks)
        )
        all_retrieved.append(retrieved)
        all_gold.append(gold_ids)
        all_permissive.append(permissive_ids)

    result = AblationResult(
        label, chunk_level, retriever_name, top_k, config.retrieval.patient_filter, "ok", n_questions=len(eval_qs)
    )
    for k in sorted(set(K_VALUES) | {top_k}):
        result.recall_lenient[k] = bootstrap_ci(per_question_hits(all_retrieved, all_permissive, k))
        result.recall_strict[k] = bootstrap_ci(per_question_hits(all_retrieved, all_gold, k))

    result.mrr_lenient = mrr_at_k(all_retrieved, all_permissive, 10)
    result.mrr_strict = mrr_at_k(all_retrieved, all_gold, 10)
    result.ndcg_lenient = ndcg_at_k(all_retrieved, all_permissive, 10)
    result.ndcg_strict = ndcg_at_k(all_retrieved, all_gold, 10)
    result.latency_p50_ms = statistics.median(latencies_ms)
    result.latency_p95_ms = sorted(latencies_ms)[int(0.95 * (len(latencies_ms) - 1))]
    result.per_question_hit_strict = {q.qid: h for q, h in zip(eval_qs, per_question_hits(all_retrieved, all_gold, top_k))}
    result.per_question_hit_lenient = {q.qid: h for q, h in zip(eval_qs, per_question_hits(all_retrieved, all_permissive, top_k))}
    return result


def ablation_result_to_row(r: AblationResult) -> dict:
    row = {
        "label": r.label,
        "chunk_level": r.chunk_level,
        "retriever": r.retriever,
        "top_k": r.top_k,
        "patient_filter": r.patient_filter,
        "status": r.status,
        "n_questions": r.n_questions,
        "mrr_lenient": round(r.mrr_lenient, 4),
        "mrr_strict": round(r.mrr_strict, 4),
        "ndcg_lenient": round(r.ndcg_lenient, 4),
        "ndcg_strict": round(r.ndcg_strict, 4),
        "latency_p50_ms": round(r.latency_p50_ms, 2),
        "latency_p95_ms": round(r.latency_p95_ms, 2),
    }
    for k, boot in r.recall_lenient.items():
        row[f"recall_lenient@{k}"] = round(boot.mean, 4)
        row[f"recall_lenient@{k}_ci_lo"] = round(boot.ci_low, 4)
        row[f"recall_lenient@{k}_ci_hi"] = round(boot.ci_high, 4)
    for k, boot in r.recall_strict.items():
        row[f"recall_strict@{k}"] = round(boot.mean, 4)
        row[f"recall_strict@{k}_ci_lo"] = round(boot.ci_low, 4)
        row[f"recall_strict@{k}_ci_hi"] = round(boot.ci_high, 4)
    # §6.5 "Lenient minus strict gap per configuration" — tabulated here for
    # every k rather than narrated for a single config, so every executed
    # row carries it, not just the one row a README happens to discuss.
    for k in sorted(set(r.recall_lenient) & set(r.recall_strict)):
        row[f"lenient_minus_strict@{k}"] = round(r.recall_lenient[k].mean - r.recall_strict[k].mean, 4)
    return row


def write_ablations_csv(results: list[AblationResult], path: str) -> None:
    rows = [ablation_result_to_row(r) for r in results]
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with open(path, "w", newline="") as f:
        writer = csv_module.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
