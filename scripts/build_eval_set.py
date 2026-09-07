#!/usr/bin/env python3
"""Regenerate data/eval_set.jsonl and data/splits.yml from the raw FHIR export.

    PYTHONPATH=src python3 scripts/build_eval_set.py

One command, per §6 ("regenerable with one command") — this script is a thin
orchestration wrapper; all the actual logic lives in evalset/*.py so it's
importable from a notebook or another script too (§8.3).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clinical_retrieval.common.types import EvalQuestion
from clinical_retrieval.corpus.chunk import build_chunks
from clinical_retrieval.corpus.notes import extract_notes
from clinical_retrieval.evalset.ground_truth import load_all_patient_data
from clinical_retrieval.evalset.grounding import annotate_grounding, grounding_report
from clinical_retrieval.evalset.hand_written import generate_hand_written
from clinical_retrieval.evalset.paraphrase import make_paraphrases
from clinical_retrieval.evalset.permissive import (
    build_encounter_to_chunk_ids,
    chunks_by_patient,
    compute_permissive_chunk_ids,
    gold_chunk_ids_for_level,
)
from clinical_retrieval.evalset.split import stratified_patient_split, write_splits
from clinical_retrieval.evalset.templates import generate_candidates

RAW_DIR = "data/raw"
OUT_EVAL_SET = "data/eval_set.jsonl"
OUT_SPLITS = "data/splits.yml"

PER_TIER_TARGET = 40
HAND_WRITTEN_TARGET = 35
N_PARAPHRASE_SOURCES = 40


def main() -> None:
    print("Loading structured FHIR into per-patient records...")
    patients = load_all_patient_data(RAW_DIR)
    print(f"  {len(patients)} patients")

    print("Extracting notes for the groundedness filter...")
    notes = extract_notes(RAW_DIR)
    note_text_by_encounter = {n.encounter_id: n.text for n in notes}

    print("Building full_note chunks (the reference chunk level for persisted gold ids)...")
    full_note_chunks = build_chunks("full_note", RAW_DIR)
    patient_chunk_index = chunks_by_patient(full_note_chunks)
    encounter_to_chunk = build_encounter_to_chunk_ids(full_note_chunks)

    print("Generating tier 1/2/3 programmatic candidates...")
    programmatic = generate_candidates(patients, per_tier_target=PER_TIER_TARGET)
    print(f"  {len(programmatic)} programmatic candidates")

    print("Generating hand-written candidates...")
    hand_written = generate_hand_written(patients, target=HAND_WRITTEN_TARGET)
    print(f"  {len(hand_written)} hand-written candidates")

    all_candidates = programmatic + hand_written

    print("Running groundedness filter (§3.2)...")
    annotate_grounding(all_candidates, note_text_by_encounter)
    report = grounding_report(all_candidates)
    print("  grounding rate by tier:", json.dumps(report["by_tier"], indent=2))

    print("Generating paraphrases for a sample of grounded, answerable programmatic questions (§3.3)...")
    grounded_answerable = [
        c for c in all_candidates
        if c["generation"] == "programmatic" and c["grounded_in_note"] and c["answerable"]
    ]
    import random

    random.Random(41).shuffle(grounded_answerable)
    paraphrase_sources = grounded_answerable[:N_PARAPHRASE_SOURCES]

    print("Assigning qids...")
    tier_counters: Counter = Counter()
    for c in all_candidates:
        tier_counters[c["tier"]] += 1
        c["qid"] = f"t{c['tier']}_{tier_counters[c['tier']]:04d}"

    paraphrased = []
    for src in paraphrase_sources:
        for p in make_paraphrases(src, src["qid"], n=1):
            p["qid"] = f"{src['qid']}_p1"
            paraphrased.append(p)
    print(f"  {len(paraphrased)} paraphrases generated (of {len(paraphrase_sources)} sources)")

    all_candidates.extend(paraphrased)

    print("Computing patient-level stratified split (§3.5)...")
    encounter_counts = {pid: len(pd.encounters) for pid, pd in patients.items()}
    splits = stratified_patient_split(encounter_counts)
    patient_to_split = {pid: fold for fold, pids in splits.items() for pid in pids}
    write_splits(splits, OUT_SPLITS, encounter_counts)
    fold_sizes = {k: len(v) for k, v in splits.items()}
    print(f"  fold sizes (patients): {fold_sizes}")

    print("Computing permissive_chunk_ids (§1.2, at the full_note reference level)...")
    eval_questions: list[EvalQuestion] = []
    for c in all_candidates:
        gold_chunk_ids = gold_chunk_ids_for_level(c["gold_encounter_ids"], "full_note", encounter_to_chunk)
        permissive_ids = sorted(
            set(gold_chunk_ids)
            | set(compute_permissive_chunk_ids(c.get("answer_terms", []), c["patient_id"], patient_chunk_index))
        )
        eval_questions.append(
            EvalQuestion(
                qid=c["qid"],
                question=c["question"],
                tier=c["tier"],
                generation=c["generation"],
                answer=c["answer"],
                gold_chunk_ids=gold_chunk_ids,
                permissive_chunk_ids=permissive_ids,
                gold_resource_ids=c.get("gold_resource_ids", []),
                patient_id=c["patient_id"],
                grounded_in_note=c.get("grounded_in_note", False),
                answerable=c.get("answerable", True),
                split=patient_to_split.get(c["patient_id"], "train"),
                paraphrase_of=c.get("paraphrase_of"),
                template_id=c.get("template_id"),
                question_type=c.get("question_type"),
                gold_encounter_ids=c.get("gold_encounter_ids", []),
                answer_terms=c.get("answer_terms", []),
            )
        )

    Path(OUT_EVAL_SET).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_EVAL_SET, "w") as f:
        for q in eval_questions:
            f.write(json.dumps(q.to_dict()) + "\n")

    print(f"\nWrote {len(eval_questions)} questions to {OUT_EVAL_SET}")
    print("By split:", dict(Counter(q.split for q in eval_questions)))
    print("By tier:", dict(Counter(q.tier for q in eval_questions)))
    print("By generation:", dict(Counter(q.generation for q in eval_questions)))
    n_true_neg = sum(1 for q in eval_questions if q.question_type == "allergy_negation")
    print(f"True negatives (allergy_negation): {n_true_neg} ({n_true_neg / len(eval_questions):.1%})")
    n_grounded = sum(1 for q in eval_questions if q.grounded_in_note)
    print(f"Grounded: {n_grounded}/{len(eval_questions)} ({n_grounded / len(eval_questions):.1%})")


if __name__ == "__main__":
    main()
