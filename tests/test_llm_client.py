"""Tests for generation/llm_client.py — the OpenAI-compatible LLM client and
its no-key fallback. No real network call is made anywhere in this file;
the one test that exercises the real `openai` SDK path mocks the SDK client
class and is skipped if the (optional) `openai` package isn't installed."""

import pytest

from clinical_retrieval.generation.llm_client import NullLLMClient, OpenAICompatibleClient

FAKE_KEY = "sk-test-do-not-leak-this-value-12345"


# --- 1. LLM client configuration -------------------------------------------

def test_client_reads_key_from_the_configured_env_var_name(monkeypatch):
    monkeypatch.setenv("CUSTOM_KEY_VAR", FAKE_KEY)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    openai = pytest.importorskip("openai")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: object())

    client = OpenAICompatibleClient("gpt-4o-mini", api_key_env="CUSTOM_KEY_VAR")
    assert client.model == "gpt-4o-mini"


def test_client_accepts_a_custom_base_url_for_other_openai_compatible_providers(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    openai = pytest.importorskip("openai")
    captured = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: captured.update(kwargs) or object())

    OpenAICompatibleClient("gpt-4o-mini", base_url="https://example-compatible-endpoint/v1")
    assert captured["base_url"] == "https://example-compatible-endpoint/v1"
    assert captured["api_key"] == FAKE_KEY


# --- 2. Missing API key behavior --------------------------------------------

def test_missing_api_key_raises_runtime_error_without_importing_openai(monkeypatch):
    """This must fail before ever touching the openai package, so retrieval-only
    installs (no `openai` package) still get a clean, immediate error here."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        OpenAICompatibleClient("gpt-4o-mini")
    assert "LLM_API_KEY" in str(exc_info.value)


def test_missing_api_key_error_never_contains_a_key_value(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        OpenAICompatibleClient("gpt-4o-mini")
    message = str(exc_info.value)
    assert FAKE_KEY not in message
    assert "sk-" not in message  # names the env var, never a value


def test_missing_api_key_respects_a_custom_env_var_name(monkeypatch):
    monkeypatch.delenv("SOME_OTHER_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        OpenAICompatibleClient("gpt-4o-mini", api_key_env="SOME_OTHER_KEY")
    assert "SOME_OTHER_KEY" in str(exc_info.value)


# --- 3. Successful mocked LLM generation (gated on the optional `openai` extra) --

def test_generate_calls_the_sdk_and_returns_its_text_without_leaking_the_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    openai = pytest.importorskip("openai")

    class FakeMessage:
        content = "Metformin [chunk_1]"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        def create(self, **kwargs):
            # The real SDK would embed the key in an Authorization header,
            # never in the request body/kwargs this test can see — assert
            # none of the visible call arguments contain it either way.
            assert FAKE_KEY not in str(kwargs)
            return type("R", (), {"choices": [FakeChoice()]})()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    client = OpenAICompatibleClient("gpt-4o-mini")
    result = client.generate("some prompt")

    assert result == "Metformin [chunk_1]"
    assert FAKE_KEY not in result


# --- 6. Retrieval-only behavior when no LLM is configured -------------------

def test_null_client_needs_no_key_and_no_network():
    client = NullLLMClient()
    result = client.generate("any prompt")
    assert "No LLM configured" in result
    assert "retrieval-only" in result.lower()


def test_null_client_result_never_contains_secret_looking_text():
    result = NullLLMClient().generate("any prompt")
    assert "sk-" not in result
    assert "LLM_API_KEY" not in result
