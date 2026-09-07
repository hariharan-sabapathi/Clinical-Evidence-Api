#!/usr/bin/env python3
"""§6.5 extra analyses + §6.4 paired significance tests, all against the
reference config (the §4.1 baseline: fixed_512, bm25, k=5 — see
configs/reference.yml) on the real test split.

    PYTHONPATH=src python3 scripts/run_extra_analyses.py

Produces:
  results/breakdown_by_tier.csv        — Recall/MRR/nDCG per tier (0/1/2/3)
  results/breakdown_by_generation.csv  — same, programmatic-origin vs hand-written
  results/within_vs_cross_patient.csv  — oracle patient-restricted vs unrestricted retrieval
  results/significance_tests.csv       — real paired bootstrap tests on real per-question results

No Hugging Face access or LLM needed — everything here is BM25 + structured
FHIR bookkeeping, run against the actual 120-patient export.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clinical_retrieval.common.config import Config
from clinical_retrieval.corpus.chunk import build_chunks
from clinical_retrieval.corpus.fhir_serialize import load_patient_names
from clinical_retrieval.eval.breakdown import compute_group_metrics, group_metrics_to_row
from clinical_retrieval.eval.metrics import paired_bootstrap_test, per_question_hits
from clinical_retrieval.evalset.paraphrase import prefer_paraphrases
from clinical_retrieval.evalset.permissive import build_encounter_to_chunk_ids, chunks_by_patient, compute_permissive_chunk_ids
from clinical_retrieval.evalset.split import load_eval_questions
from clinical_retrieval.retrieval.bm25 import BM25Retriever
from clinical_retrieval.retrieval.filters import PatientFilteredRetriever, PatientNameResolver

RAW_DIR = "data/raw"
EVAL_SET_PATH = "data/eval_set.jsonl"
SPLITS_PATH = "data/splits.yml"
K = 5


def origin(q) -> str:
    """§6.5 wants 'programmatic vs. hand-written'. A paraphrase is a
    paraphrase *of* a programmatic template, so it counts as programmatic
    origin here, not as a third bucket."""
    return "hand_written" if q.generation == "hand_written" else "programmatic"


def gold_and_permissive(q, encounter_to_chunk, patient_chunks) -> tuple[set[str], set[str]]:
    gold = set()
    for enc_id in q.gold_encounter_ids:
        gold.update(encounter_to_chunk.get(enc_id, []))
    permissive = set(gold) | set(compute_permissive_chunk_ids(q.answer_terms, q.patient_id, patient_chunks))
    return gold, permissive


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    ref = Config.load("configs/reference.yml")
    questions = prefer_paraphrases(load_eval_questions(EVAL_SET_PATH, SPLITS_PATH))
    eval_qs = [q for q in questions if q.split == "test" and q.answerable]
    print(f"Reference config: chunk_level={ref.chunk_level}, retriever={ref.retrieval.retriever}, top_k={ref.retrieval.top_k}")
    print(f"{len(eval_qs)} answerable test questions (paraphrase-preferred, splits.yml-authoritative)")

    chunks = build_chunks(ref.chunk_level, RAW_DIR)
    patient_chunks = chunks_by_patient(chunks)
    encounter_to_chunk = build_encounter_to_chunk_ids(chunks)
    patient_names = load_patient_names(RAW_DIR)
    retriever = BM25Retriever(chunks)

    pool_k = 50
    all_retrieved = [retriever.retrieve(q.question, pool_k) for q in eval_qs]
    all_gold, all_permissive = [], []
    for q in eval_qs:
        gold, permissive = gold_and_permissive(q, encounter_to_chunk, patient_chunks)
        all_gold.append(gold)
        all_permissive.append(permissive)

    # =====================================================================
    # §6.5 — per-tier breakdown
    # =====================================================================
    print("\n=== Per-tier breakdown (Recall@5, MRR@10, nDCG@10) ===")
    by_tier: dict[int, list[int]] = defaultdict(list)
    for i, q in enumerate(eval_qs):
        by_tier[q.tier].append(i)
    tier_rows = []
    for tier in sorted(by_tier):
        gm = compute_group_metrics(f"tier_{tier}", by_tier[tier], all_retrieved, all_gold, all_permissive, k=K)
        row = group_metrics_to_row(gm)
        tier_rows.append(row)
        print(f"  tier {tier} (n={row['n']}): recall_strict@5={row['recall_strict@5']}  recall_lenient@5={row['recall_lenient@5']}")
    write_csv("results/breakdown_by_tier.csv", tier_rows)
    print("Wrote results/breakdown_by_tier.csv")

    # =====================================================================
    # §6.5 — programmatic vs. hand-written breakdown
    # =====================================================================
    print("\n=== Programmatic vs. hand-written breakdown ===")
    by_origin: dict[str, list[int]] = defaultdict(list)
    for i, q in enumerate(eval_qs):
        by_origin[origin(q)].append(i)
    gen_rows = []
    for group in sorted(by_origin):
        gm = compute_group_metrics(group, by_origin[group], all_retrieved, all_gold, all_permissive, k=K)
        row = group_metrics_to_row(gm)
        gen_rows.append(row)
        print(f"  {group} (n={row['n']}): recall_strict@5={row['recall_strict@5']}  recall_lenient@5={row['recall_lenient@5']}")
    write_csv("results/breakdown_by_generation.csv", gen_rows)
    print("Wrote results/breakdown_by_generation.csv")

    # =====================================================================
    # §6.5 — within-patient vs. cross-patient (§1.1)
    # "cross-patient" = unrestricted retrieval over the whole corpus (already
    # computed above as all_retrieved). "within-patient" = an *oracle* filter
    # to the question's own correct patient_id (not the name-resolved
    # PatientFilteredRetriever — that conflates name-resolution success with
    # encounter-selection quality; the oracle isolates encounter-selection
    # alone, which is what §1.1's claim is actually about).
    # =====================================================================
    print("\n=== Within-patient (oracle) vs. cross-patient (unrestricted) ===")
    within_retrieved = []
    for q in eval_qs:
        allowed = {c.chunk_id for c in patient_chunks.get(q.patient_id, [])}
        within_retrieved.append(retriever.retrieve(q.question, pool_k, allowed_chunk_ids=allowed))

    cross_gm = compute_group_metrics("cross_patient", list(range(len(eval_qs))), all_retrieved, all_gold, all_permissive, k=K)
    within_gm = compute_group_metrics("within_patient", list(range(len(eval_qs))), within_retrieved, all_gold, all_permissive, k=K)
    scope_rows = [group_metrics_to_row(cross_gm), group_metrics_to_row(within_gm)]
    write_csv("results/within_vs_cross_patient.csv", scope_rows)
    for row in scope_rows:
        print(f"  {row['group']}: recall_strict@5={row['recall_strict@5']}  recall_lenient@5={row['recall_lenient@5']}")
    print("Wrote results/within_vs_cross_patient.csv")

    # =====================================================================
    # §6.4 — real paired bootstrap significance tests
    # =====================================================================
    print("\n=== Paired bootstrap significance tests (real per-question results) ===")
    sig_rows = []

    def add_test(comparison: str, values_a: list[float], values_b: list[float], n_a: int, n_b: int) -> None:
        result = paired_bootstrap_test(values_a, values_b, n_resamples=1000)
        sig_rows.append({
            "comparison": comparison,
            "n": len(values_a),
            "mean_a": round(sum(values_a) / len(values_a), 4),
            "mean_b": round(sum(values_b) / len(values_b), 4),
            "diff_mean": round(result.diff_mean, 4),
            "ci_lo": round(result.ci_low, 4),
            "ci_hi": round(result.ci_high, 4),
            "significant_95pct": result.significant,
        })
        verdict = "SIGNIFICANT" if result.significant else "not significant"
        print(f"  {comparison}: diff={result.diff_mean:+.3f}  95% CI [{result.ci_low:+.3f}, {result.ci_high:+.3f}]  -> {verdict}")

    # (a) within-patient vs cross-patient, strict hit@5 — the §1.1 claim itself
    add_test(
        "within_patient_vs_cross_patient (recall_strict@5)",
        per_question_hits(within_retrieved, all_gold, K),
        per_question_hits(all_retrieved, all_gold, K),
        len(eval_qs), len(eval_qs),
    )

    # (b) top_k=5 vs top_k=10, same ranked lists (already retrieved to pool_k=50)
    add_test(
        "top_k=10_vs_top_k=5 (recall_strict)",
        per_question_hits(all_retrieved, all_gold, 10),
        per_question_hits(all_retrieved, all_gold, 5),
        len(eval_qs), len(eval_qs),
    )

    # (c) reference baseline (fixed_512) vs full_note chunk_level
    full_note_chunks = build_chunks("full_note", RAW_DIR)
    full_note_patient_chunks = chunks_by_patient(full_note_chunks)
    full_note_encounter_to_chunk = build_encounter_to_chunk_ids(full_note_chunks)
    full_note_retriever = BM25Retriever(full_note_chunks)
    full_note_retrieved, full_note_gold = [], []
    for q in eval_qs:
        r = full_note_retriever.retrieve(q.question, pool_k)
        full_note_retrieved.append(r)
        g, _ = gold_and_permissive(q, full_note_encounter_to_chunk, full_note_patient_chunks)
        full_note_gold.append(g)
    add_test(
        "baseline_fixed_512_vs_full_note (recall_strict@5)",
        per_question_hits(all_retrieved, all_gold, K),
        per_question_hits(full_note_retrieved, full_note_gold, K),
        len(eval_qs), len(eval_qs),
    )

    # (d) bm25 (unfiltered, baseline) vs bm25+patient_filter (name-resolved, realistic deployment)
    resolver = PatientNameResolver(patient_names)
    filtered_retriever = PatientFilteredRetriever(retriever, resolver, chunks)
    filtered_retrieved = [filtered_retriever.retrieve(q.question, pool_k) for q in eval_qs]
    add_test(
        "bm25+patient_filter_vs_bm25 (recall_strict@5)",
        per_question_hits(filtered_retrieved, all_gold, K),
        per_question_hits(all_retrieved, all_gold, K),
        len(eval_qs), len(eval_qs),
    )

    write_csv("results/significance_tests.csv", sig_rows)
    print("Wrote results/significance_tests.csv")


if __name__ == "__main__":
    main()
