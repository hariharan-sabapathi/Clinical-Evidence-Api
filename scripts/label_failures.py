#!/usr/bin/env python3
"""§7 — label retrieval-side failures on the test split's failing questions
under the reference config (the §4.1 baseline: fixed_512, bm25, k=5 — see
configs/reference.yml).

    PYTHONPATH=src python3 scripts/label_failures.py

Generation-side codes (G-*) need an actual generated answer, which needs an
LLM this sandbox doesn't have configured — see README "Environment
constraints". Those rows are written with code="NOT_RUN_NO_LLM" rather than
guessed.

Splits come from data/splits.yml (§3.5), and raw templates with a paraphrase
are dropped in favor of it (§3.3) — same question set eval/runner.py uses.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clinical_retrieval.common.config import Config
from clinical_retrieval.corpus.chunk import build_chunks
from clinical_retrieval.eval.failure_taxonomy import label_retrieval_failure
from clinical_retrieval.evalset.paraphrase import prefer_paraphrases
from clinical_retrieval.evalset.permissive import build_encounter_to_chunk_ids, chunks_by_patient, compute_permissive_chunk_ids
from clinical_retrieval.evalset.split import load_eval_questions
from clinical_retrieval.retrieval.bm25 import BM25Retriever

RAW_DIR = "data/raw"
EVAL_SET_PATH = "data/eval_set.jsonl"
SPLITS_PATH = "data/splits.yml"
TOP_K = 5
POOL = 50


def main() -> None:
    reference_chunk_level = Config.load("configs/reference.yml").chunk_level

    questions = prefer_paraphrases(load_eval_questions(EVAL_SET_PATH, SPLITS_PATH))
    test_questions = [q for q in questions if q.split == "test"]

    chunks = build_chunks(reference_chunk_level, RAW_DIR)
    retriever = BM25Retriever(chunks)
    patient_chunks = chunks_by_patient(chunks)
    encounter_to_chunk = build_encounter_to_chunk_ids(chunks)

    rows = []
    for q in test_questions:
        gold_ids = set()
        for enc_id in q.gold_encounter_ids:
            gold_ids.update(encounter_to_chunk.get(enc_id, []))
        permissive_ids = set(gold_ids) | set(compute_permissive_chunk_ids(q.answer_terms, q.patient_id, patient_chunks))

        pool = retriever.retrieve(q.question, POOL)
        top_k = pool[:TOP_K]
        if not q.answerable:
            code = "Q-AMBIG"
        else:
            code = label_retrieval_failure(q, top_k, pool, gold_ids, permissive_ids)

        if code is None:
            continue  # success at k, nothing to label

        rows.append({
            "qid": q.qid,
            "question_type": q.question_type,
            "question": q.question,
            "patient_id": q.patient_id,
            "code": code,
            "code_description": {
                "R-MISS": "Gold chunk not in top-k", "R-RANK": "Retrieved but ranked below distractors",
                "R-PATIENT": "Retrieved the wrong patient entirely", "R-TEMPORAL": "Right patient and fact, wrong encounter",
                "Q-AMBIG": "Question genuinely ambiguous — eval set defect",
            }.get(code, code),
            "top1_chunk_id": top_k[0].chunk.chunk_id if top_k else "",
            "top1_patient_id": top_k[0].chunk.patient_id if top_k else "",
            "top5_chunk_ids": "; ".join(sc.chunk.chunk_id for sc in top_k),
            "gold_chunk_ids": "; ".join(sorted(gold_ids)),
            "answer_reference": q.answer,
            "generation_code": "NOT_RUN_NO_LLM",
        })

    Path("results").mkdir(exist_ok=True)
    with open("results/failure_labels.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    from collections import Counter

    counts = Counter(r["code"] for r in rows)
    print(f"Reference chunk level: {reference_chunk_level}")
    print(f"Labeled {len(rows)} failing test questions (of {len(test_questions)} total test questions)")
    for code, n in counts.most_common():
        print(f"  {code}: {n}")
    print("Wrote results/failure_labels.csv")


if __name__ == "__main__":
    main()
