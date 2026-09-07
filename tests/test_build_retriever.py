"""Regression test for eval/runner.py::build_retriever after the project was
simplified to BM25-only (dense/hybrid/rerank/query_rewrite removed). It must
still build a plain BM25Retriever, still wrap it with the patient filter
when asked, and now reject any other retriever name with a plain ValueError
rather than the old RetrieverUnavailable (which no longer exists — the
various "not runnable in this sandbox" failure modes it used to catch don't
apply to anything left in the codebase)."""

import pytest

from clinical_retrieval.common.config import Config
from clinical_retrieval.common.types import Chunk
from clinical_retrieval.eval.runner import build_retriever
from clinical_retrieval.retrieval.bm25 import BM25Retriever
from clinical_retrieval.retrieval.filters import PatientFilteredRetriever


def _chunks():
    return [Chunk(chunk_id="c1", chunk_level="full_note", patient_id="p1", text="metformin note")]


def test_build_retriever_returns_plain_bm25_by_default():
    config = Config()
    retriever = build_retriever("bm25", _chunks(), config)
    assert isinstance(retriever, BM25Retriever)


def test_build_retriever_wraps_with_patient_filter_when_requested():
    config = Config()
    config.retrieval.patient_filter = True
    retriever = build_retriever("bm25", _chunks(), config, patient_names={"p1": "Jordan55 Smith"})
    assert isinstance(retriever, PatientFilteredRetriever)


def test_build_retriever_rejects_unknown_retriever_names():
    config = Config()
    for name in ("dense", "hybrid", "hybrid_rerank", "query_rewrite"):
        with pytest.raises(ValueError):
            build_retriever(name, _chunks(), config)


def test_build_retriever_patient_filter_requires_patient_names():
    config = Config()
    config.retrieval.patient_filter = True
    with pytest.raises(ValueError):
        build_retriever("bm25", _chunks(), config, patient_names=None)
