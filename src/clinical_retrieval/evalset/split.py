"""§3.5 — split by patient, stratified by encounter count, so a naive 60/20/20
doesn't put most of the corpus (encounter counts range 10-672) in one fold."""

from __future__ import annotations

import json

import yaml

from ..common.types import EvalQuestion


def stratified_patient_split(
    encounter_counts: dict[str, int], train_frac: float = 0.6, dev_frac: float = 0.2, seed: int = 7
) -> dict[str, list[str]]:
    patients_sorted = sorted(encounter_counts.items(), key=lambda kv: kv[1])
    folds = {"train": [], "dev": [], "test": []}
    fold_sizes = {
        "train": round(len(patients_sorted) * train_frac),
        "dev": round(len(patients_sorted) * dev_frac),
    }
    fold_sizes["test"] = len(patients_sorted) - fold_sizes["train"] - fold_sizes["dev"]

    # Round-robin across a stack of [train]*t + [dev]*d + [test]*te repeated,
    # walking the encounter-count-sorted list so every fold gets a spread of
    # low-, medium-, and high-encounter-count patients rather than clumping.
    cycle = (
        ["train"] * max(1, round(train_frac * 10))
        + ["dev"] * max(1, round(dev_frac * 10))
        + ["test"] * max(1, round((1 - train_frac - dev_frac) * 10))
    )
    i = 0
    for patient_id, _count in patients_sorted:
        # Respect target fold sizes once a fold fills up.
        attempts = 0
        fold = cycle[i % len(cycle)]
        while len(folds[fold]) >= fold_sizes.get(fold, len(patients_sorted)) and attempts < len(cycle):
            i += 1
            fold = cycle[i % len(cycle)]
            attempts += 1
        folds[fold].append(patient_id)
        i += 1
    return folds


def write_splits(splits: dict[str, list[str]], path: str, encounter_counts: dict[str, int]) -> None:
    doc = {
        "description": "Patient-level split, stratified by encounter count (§3.5).",
        "fold_sizes": {k: len(v) for k, v in splits.items()},
        "patients": splits,
        "encounter_counts_by_patient": encounter_counts,
    }
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False)


def load_splits(path: str) -> dict[str, list[str]]:
    with open(path) as f:
        doc = yaml.safe_load(f)
    return doc["patients"]


def patient_to_split(path: str) -> dict[str, str]:
    """Inverted view of load_splits: patient_id -> fold name."""
    splits = load_splits(path)
    return {patient_id: fold for fold, patient_ids in splits.items() for patient_id in patient_ids}


def apply_splits(questions: list, path: str) -> list:
    """Overwrite each question's `.split` from data/splits.yml, keyed by
    patient_id, so the file — not whatever `split` value happened to get
    baked into eval_set.jsonl at generation time — is the authority every
    consumer actually reads. Mutates and returns `questions`; raises if a
    question's patient isn't in any fold, since that means the eval set and
    the splits file have drifted out of sync silently."""
    mapping = patient_to_split(path)
    missing = set()
    for q in questions:
        fold = mapping.get(q.patient_id)
        if fold is None:
            missing.add(q.patient_id)
            continue
        q.split = fold
    if missing:
        raise ValueError(
            f"{len(missing)} patient(s) in the eval set have no fold in {path}: {sorted(missing)[:5]}..."
        )
    return questions


def load_eval_questions(eval_set_path: str, splits_path: str) -> list[EvalQuestion]:
    """The one place every script should load questions from: reads
    eval_set.jsonl, then immediately overwrites `.split` from splits.yml so
    the file is the actual source of truth, not a value that happened to be
    embedded at generation time (§3.5)."""
    with open(eval_set_path) as f:
        questions = [EvalQuestion.from_dict(json.loads(line)) for line in f]
    return apply_splits(questions, splits_path)
