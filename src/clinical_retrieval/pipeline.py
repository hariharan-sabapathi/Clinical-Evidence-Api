"""The one library entrypoint everything else (CLI, eval harness, a future
Project 3 service) is a thin wrapper over — see §8.3. `Pipeline.answer`
takes an explicit `Config`, never reads globals or the environment, and
returns the same `Answer` object whether it's called from a script, a
notebook, or the CLI in query.py.
"""

from __future__ import annotations

from .common.config import Config
from .common.types import Answer
from .corpus.chunk import build_chunks
from .corpus.fhir_serialize import load_patient_names
from .eval.runner import build_retriever
from .generation.generate import generate_answer
from .generation.llm_client import LLMClient, NullLLMClient


class Pipeline:
    def __init__(self, config: Config, raw_dir: str = "data/raw", llm_client: LLMClient | None = None):
        self.config = config
        self.raw_dir = raw_dir
        self.chunks = build_chunks(config.chunk_level, raw_dir)
        self.patient_names = load_patient_names(raw_dir)
        self.retriever = build_retriever(config.retrieval.retriever, self.chunks, config, self.patient_names)
        self.llm_client = llm_client or NullLLMClient()

    def answer(self, question: str) -> Answer:
        retrieved = self.retriever.retrieve(question, self.config.retrieval.top_k)
        result = generate_answer(question, retrieved, self.llm_client)
        retrieval_timing = getattr(self.retriever, "last_timing_ms", None) or {"retrieve": 0.0}
        result.timing_ms = {**retrieval_timing, **result.timing_ms}
        return result


def answer(question: str, config: Config, raw_dir: str = "data/raw", llm_client: LLMClient | None = None) -> Answer:
    """Convenience one-shot call. Builds a fresh Pipeline (and therefore a
    fresh index) every call — fine for a script or a notebook cell, wasteful
    for a loop or a service. Construct `Pipeline` directly and reuse it in
    those cases (see query.py's REPL and index-loading-separated-from-
    building per §8.3)."""
    return Pipeline(config, raw_dir=raw_dir, llm_client=llm_client).answer(question)
