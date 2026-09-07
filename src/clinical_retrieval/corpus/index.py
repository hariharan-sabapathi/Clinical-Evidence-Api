"""Persist chunks to disk and build retrieval indexes over them.

Index *building* is separated from index *loading* (build once here, load
cheaply in retrieval/*.py) so a long-running process — the CLI, a future
service — never rebuilds an index per query.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..common.types import Chunk

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Shared lowercase word tokenizer for BM25 and grounding string-matching.
    Deliberately simple (no subword/BPE model) — this runs everywhere without
    a model download, and BM25 doesn't benefit from a learned vocabulary."""
    return _WORD_RE.findall(text.lower())


def save_chunks(chunks: list[Chunk], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.to_dict()) + "\n")


def load_chunks(path: str | Path) -> list[Chunk]:
    chunks = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(Chunk.from_dict(json.loads(line)))
    return chunks


def chunk_store_path(chunk_dir: str | Path, chunk_level: str) -> Path:
    return Path(chunk_dir) / f"{chunk_level}.jsonl"
