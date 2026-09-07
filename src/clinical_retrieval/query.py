"""§8.1 CLI query tool.

    python -m clinical_retrieval.query --config configs/reference.yml \\
        "what medications was Shantelle354 on during her 2019 visits?"

A thin wrapper over pipeline.Pipeline — all the logic lives in the library
(§8.3); this just parses arguments, prints, and times.
"""

from __future__ import annotations

import argparse
import time

from .common.config import Config
from .generation.llm_client import LLMClient, NullLLMClient, OpenAICompatibleClient
from .pipeline import Pipeline


def _make_llm_client(config: Config) -> LLMClient:
    if config.llm_model == "not-configured":
        return NullLLMClient()
    try:
        return OpenAICompatibleClient(config.llm_model, base_url=config.llm_base_url)
    except RuntimeError as exc:
        print(f"[warning] {exc} — falling back to retrieval-only output.")
        return NullLLMClient()


def run_query(question: str, config: Config, raw_dir: str = "data/raw") -> None:
    t0 = time.perf_counter()
    pipeline = Pipeline(config, raw_dir=raw_dir, llm_client=_make_llm_client(config))
    load_ms = (time.perf_counter() - t0) * 1000

    result = pipeline.answer(question)

    print(f"RETRIEVED ({config.retrieval.retriever}, chunk_level={config.chunk_level}, "
          f"k={config.retrieval.top_k})")
    for sc in result.retrieved:
        c = sc.chunk
        snippet = c.text[:80].replace("\n", " ")
        print(f"  {sc.rank}. {c.chunk_id}  score {sc.score:.3f}  {c.encounter_date or ''}  "
              f"{c.encounter_type or c.section_name or ''}")
        print(f'     "{snippet}..."')

    print("\nANSWER")
    print(f"  {result.text}")
    if result.citations:
        print(f"  {result.citations}")

    timing_parts = [f"index_load {load_ms:.0f}ms"]
    timing_parts += [f"{stage} {ms:.0f}ms" for stage, ms in result.timing_ms.items()]
    print("\nTIMING  " + " | ".join(timing_parts))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--config", default="configs/reference.yml")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--chunk-level", default=None, help="Override the config's chunk_level")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--patient-filter", action="store_true", help="Restrict retrieval to the named patient")
    args = parser.parse_args()

    config = Config.load(args.config)
    if args.chunk_level:
        config.chunk_level = args.chunk_level
    if args.top_k:
        config.retrieval.top_k = args.top_k
    if args.patient_filter:
        config.retrieval.patient_filter = True

    run_query(args.question, config, raw_dir=args.raw_dir)


if __name__ == "__main__":
    main()
