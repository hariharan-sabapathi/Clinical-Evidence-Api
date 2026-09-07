"""Regression tests for evalset/split.py's apply_splits/load_eval_questions —
the fix that makes data/splits.yml the actual source of truth the pipeline
reads, instead of trusting whatever `split` value got baked into
eval_set.jsonl at generation time (§3.5)."""

import json

import pytest
import yaml

from clinical_retrieval.common.types import EvalQuestion
from clinical_retrieval.evalset.split import apply_splits, load_eval_questions, patient_to_split


def _question(qid, patient_id, split="train"):
    return EvalQuestion(
        qid=qid, question="q", tier=1, generation="programmatic", answer="a",
        gold_chunk_ids=[], permissive_chunk_ids=[], gold_resource_ids=[],
        patient_id=patient_id, grounded_in_note=True, answerable=True, split=split,
    )


def _write_splits(tmp_path, splits):
    path = tmp_path / "splits.yml"
    with open(path, "w") as f:
        yaml.safe_dump({"patients": splits}, f)
    return str(path)


def test_patient_to_split_inverts_the_fold_mapping(tmp_path):
    path = _write_splits(tmp_path, {"train": ["p1", "p2"], "dev": ["p3"], "test": ["p4"]})
    mapping = patient_to_split(path)
    assert mapping == {"p1": "train", "p2": "train", "p3": "dev", "p4": "test"}


def test_apply_splits_overrides_a_wrong_embedded_split_field(tmp_path):
    """The core regression: a question embedded with the WRONG split (as if
    eval_set.jsonl and splits.yml had drifted) must end up with the correct
    one after apply_splits, proving the file — not the embedded value — wins."""
    path = _write_splits(tmp_path, {"train": [], "dev": [], "test": ["p1"]})
    q = _question("q1", "p1", split="train")  # deliberately wrong
    apply_splits([q], path)
    assert q.split == "test"


def test_apply_splits_raises_on_a_patient_missing_from_the_splits_file(tmp_path):
    path = _write_splits(tmp_path, {"train": ["p1"], "dev": [], "test": []})
    q = _question("q1", "unknown_patient")
    with pytest.raises(ValueError):
        apply_splits([q], path)


def test_load_eval_questions_reads_jsonl_and_applies_splits(tmp_path):
    splits_path = _write_splits(tmp_path, {"train": [], "dev": [], "test": ["p1"]})
    eval_set_path = tmp_path / "eval_set.jsonl"
    q = _question("q1", "p1", split="train")  # wrong on purpose
    with open(eval_set_path, "w") as f:
        f.write(json.dumps(q.to_dict()) + "\n")

    loaded = load_eval_questions(str(eval_set_path), splits_path)
    assert len(loaded) == 1
    assert loaded[0].split == "test"  # came from splits.yml, not the embedded "train"
