"""Tests for query.py::_make_llm_client — the switch between real generation
and retrieval-only NullLLMClient, and the CLI's no-key fallback path."""

import pytest

from clinical_retrieval.common.config import Config
from clinical_retrieval.generation.llm_client import NullLLMClient, OpenAICompatibleClient
from clinical_retrieval.query import _make_llm_client

FAKE_KEY = "sk-test-do-not-leak-this-value-12345"


# --- 6. Retrieval-only behavior when no LLM is configured -------------------

def test_not_configured_sentinel_returns_null_client_without_touching_env(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    config = Config(llm_model="not-configured")
    client = _make_llm_client(config)
    assert isinstance(client, NullLLMClient)


def test_real_model_but_no_key_falls_back_to_null_client(monkeypatch, capsys):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    config = Config(llm_model="gpt-4o-mini")
    client = _make_llm_client(config)
    assert isinstance(client, NullLLMClient)


# --- 7. No secret/API key appearing in logs or output -----------------------

def test_fallback_warning_names_the_env_var_but_never_a_key_value(monkeypatch, capsys):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    config = Config(llm_model="gpt-4o-mini")
    _make_llm_client(config)
    printed = capsys.readouterr().out
    assert "LLM_API_KEY" in printed
    assert FAKE_KEY not in printed
    assert "sk-" not in printed


def test_configured_model_with_key_present_builds_the_real_client(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    openai = pytest.importorskip("openai")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: object())

    config = Config(llm_model="gpt-4o-mini")
    client = _make_llm_client(config)
    assert isinstance(client, OpenAICompatibleClient)


def test_configured_model_with_key_present_never_prints_the_key(monkeypatch, capsys):
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    openai = pytest.importorskip("openai")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: object())

    config = Config(llm_model="gpt-4o-mini")
    _make_llm_client(config)
    printed = capsys.readouterr().out
    assert FAKE_KEY not in printed
