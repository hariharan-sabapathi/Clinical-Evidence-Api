"""Regression test locking in the config simplification: RetrievalConfig
should only carry fields the surviving BM25-only pipeline actually reads.
Fields for removed components (embedding_model, cross_encoder_model,
rerank_pool, rrf_k) must not silently reappear."""

import dataclasses

from clinical_retrieval.common.config import Config, RetrievalConfig


def test_retrieval_config_has_no_removed_fields():
    field_names = {f.name for f in dataclasses.fields(RetrievalConfig)}
    assert field_names == {"retriever", "top_k", "patient_filter"}


def test_config_has_no_unused_dense_index_dirs():
    field_names = {f.name for f in dataclasses.fields(Config)}
    assert "chunk_dir" not in field_names
    assert "index_dir" not in field_names


def test_config_defaults_to_the_bm25_baseline():
    config = Config()
    assert config.chunk_level == "fixed_512"
    assert config.retrieval.retriever == "bm25"
    assert config.retrieval.top_k == 5
    assert config.retrieval.patient_filter is False
