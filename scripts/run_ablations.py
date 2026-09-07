#!/usr/bin/env python3
"""Run the ablation grid (§6.2): one axis at a time from the fixed reference
config, not the full cross-product. The reference config IS the §4.1
baseline (fixed_512 chunking, BM25, top-5) — see configs/reference.yml.
BM25 is the only retriever in this project's scope, so the grid ablates
chunk_level, the patient filter, and top_k — every row here is real,
executed BM25 output, nothing skipped for missing external dependencies.

    PYTHONPATH=src python3 scripts/run_ablations.py

Evaluates on the paraphrase-preferred question set (§3.3): for any base
question that has a paraphrase, the raw template is dropped from the
evaluated set and only the paraphrase counts — see
evalset/paraphrase.py::prefer_paraphrases.

Splits are read from data/splits.yml, not trusted from eval_set.jsonl's
embedded `split` field (§3.5) — see evalset/split.py::load_eval_questions.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clinical_retrieval.common.config import Config
from clinical_retrieval.corpus.chunk import CHUNK_LEVELS, build_chunks
from clinical_retrieval.corpus.fhir_serialize import load_patient_names
from clinical_retrieval.eval.metrics import per_question_hits
from clinical_retrieval.eval.runner import run_config, write_ablations_csv
from clinical_retrieval.evalset.paraphrase import prefer_paraphrases
from clinical_retrieval.evalset.split import load_eval_questions

RAW_DIR = "data/raw"
EVAL_SET_PATH = "data/eval_set.jsonl"
SPLITS_PATH = "data/splits.yml"


def reference_config() -> Config:
    return Config.load("configs/reference.yml")


def main() -> None:
    raw_questions = load_eval_questions(EVAL_SET_PATH, SPLITS_PATH)
    questions = prefer_paraphrases(raw_questions)
    print(f"{len(raw_questions)} eval questions loaded (splits from {SPLITS_PATH}); "
          f"{len(raw_questions) - len(questions)} raw templates dropped in favor of their paraphrase "
          f"(§3.3) -> {len(questions)} evaluated")
    print(f"  test split has {sum(1 for q in questions if q.split == 'test' and q.answerable)} answerable questions")

    chunk_cache: dict[str, list] = {}

    def chunks_for(level: str):
        if level not in chunk_cache:
            print(f"  building {level} chunks...")
            chunk_cache[level] = build_chunks(level, RAW_DIR)
        return chunk_cache[level]

    patient_names = load_patient_names(RAW_DIR)
    results = []

    # --- Axis 1: chunk_level, reference retriever/top_k/patient_filter ---
    print("\n=== chunk_level axis (reference = fixed_512, the §4.1 baseline) ===")
    for level in CHUNK_LEVELS:
        cfg = reference_config()
        cfg.chunk_level = level
        label = f"chunk_level={level}" + (" [BASELINE §4.1]" if level == reference_config().chunk_level else "")
        r = run_config(
            questions, level, cfg.retrieval.retriever, cfg.retrieval.top_k, cfg,
            raw_dir=RAW_DIR, patient_names=patient_names, chunks=chunks_for(level),
            label=label,
        )
        print(f"  {r.label}: {r.status}" + (f"  recall_strict@5={r.recall_strict.get(5).mean:.3f}" if r.status == "ok" else ""))
        results.append(r)

    # --- Axis 2: patient filter, reference chunk_level/retriever/top_k ---
    print("\n=== patient filter ===")
    cfg = reference_config()
    cfg.retrieval.patient_filter = True
    r = run_config(
        questions, cfg.chunk_level, cfg.retrieval.retriever, cfg.retrieval.top_k, cfg,
        raw_dir=RAW_DIR, patient_names=patient_names, chunks=chunks_for(cfg.chunk_level),
        label="retriever=bm25+patient_filter",
    )
    print(f"  {r.label}: {r.status}" + (f"  recall_strict@5={r.recall_strict.get(5).mean:.3f}" if r.status == "ok" else ""))
    results.append(r)

    # --- Axis 3: top_k, reference chunk_level/retriever ---
    print("\n=== top_k axis ===")
    for k in (1, 3, 5, 10, 20):
        cfg = reference_config()
        cfg.retrieval.top_k = k
        r = run_config(
            questions, cfg.chunk_level, cfg.retrieval.retriever, k, cfg,
            raw_dir=RAW_DIR, patient_names=patient_names, chunks=chunks_for(cfg.chunk_level),
            label=f"top_k={k}",
        )
        print(f"  {r.label}: {r.status}" + (f"  recall_strict@{k}={r.recall_strict.get(k).mean:.3f}" if r.status == "ok" else ""))
        results.append(r)

    Path("results").mkdir(exist_ok=True)
    write_ablations_csv(results, "results/ablations.csv")
    print(f"\nWrote results/ablations.csv ({len(results)} rows)")

    # --- Retrieval depth curve for the reference config (§6.5) ---
    ref_level = reference_config().chunk_level
    print(f"\nComputing retrieval depth curve (k=1..50) for the reference config ({ref_level})...")
    from clinical_retrieval.evalset.permissive import build_encounter_to_chunk_ids, chunks_by_patient, compute_permissive_chunk_ids
    from clinical_retrieval.retrieval.bm25 import BM25Retriever

    ref_chunks = chunks_for(ref_level)
    retriever = BM25Retriever(ref_chunks)
    patient_chunks = chunks_by_patient(ref_chunks)
    encounter_to_chunk = build_encounter_to_chunk_ids(ref_chunks)
    eval_qs = [q for q in questions if q.split == "test" and q.answerable]

    all_retrieved = [retriever.retrieve(q.question, 50) for q in eval_qs]
    all_gold = []
    all_permissive = []
    for q in eval_qs:
        gold = set()
        for enc_id in q.gold_encounter_ids:
            gold.update(encounter_to_chunk.get(enc_id, []))
        permissive = set(gold) | set(compute_permissive_chunk_ids(q.answer_terms, q.patient_id, patient_chunks))
        all_gold.append(gold)
        all_permissive.append(permissive)

    with open("results/depth_curve.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["k", "recall_lenient", "recall_strict"])
        for k in range(1, 51):
            lenient = sum(per_question_hits(all_retrieved, all_permissive, k)) / len(eval_qs)
            strict = sum(per_question_hits(all_retrieved, all_gold, k)) / len(eval_qs)
            writer.writerow([k, round(lenient, 4), round(strict, 4)])
    print("Wrote results/depth_curve.csv")


if __name__ == "__main__":
    main()
