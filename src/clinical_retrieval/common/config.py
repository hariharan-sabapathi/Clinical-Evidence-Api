"""Config loading. Library code takes explicit config objects — never reads
globals or the environment directly — so callers (CLI, tests, notebooks,
a future service) all go through the same path."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RetrievalConfig:
    retriever: str = "bm25"  # only bm25 is implemented — see eval/runner.py::build_retriever
    top_k: int = 5
    patient_filter: bool = False


@dataclass
class Config:
    chunk_level: str = "fixed_512"
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    data_dir: str = "data"
    raw_dir: str = "data/raw"
    eval_set_path: str = "data/eval_set.jsonl"
    splits_path: str = "data/splits.yml"
    llm_model: str = "not-configured"
    llm_base_url: str | None = None

    @staticmethod
    def load(path: str | Path) -> "Config":
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        retrieval_raw = raw.pop("retrieval", {}) or {}
        return Config(retrieval=RetrievalConfig(**retrieval_raw), **raw)


def project_root() -> Path:
    """Repo root, resolved from this file's location (not the CWD)."""
    return Path(__file__).resolve().parents[3]
