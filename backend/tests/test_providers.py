"""Tests for backend/providers.py: model selection and per-attempt tracing."""

import asyncio

import pytest
from strands.models.litellm import LiteLLMModel

from backend.providers import (
    MODELS_AUTHENTICATED,
    MODELS_PUBLIC,
    TracedLiteLLMModel,
    build_model,
    model_ids,
)


def test_public_requests_only_get_the_small_model():
    assert model_ids(authenticated=False) == ["groq/openai/gpt-oss-20b"]


def test_authenticated_requests_prefer_the_large_model():
    assert model_ids(authenticated=True)[0] == "groq/openai/gpt-oss-120b"
    assert model_ids(authenticated=True)[-1] == "groq/openai/gpt-oss-20b"


def test_build_model_requires_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        build_model(authenticated=False)


@pytest.mark.parametrize("authenticated,expected", [(False, MODELS_PUBLIC), (True, MODELS_AUTHENTICATED)])
def test_build_model_candidates(monkeypatch, authenticated, expected):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    router = build_model(authenticated)
    assert [c.model.get_config()["model_id"] for c in router.candidates] == expected


def _collect(model, messages):
    async def run():
        return [e async for e in model.stream(messages)]

    return asyncio.run(run())


def test_traced_model_reports_success(monkeypatch):
    async def fake_stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        yield {"messageStart": {"role": "assistant"}}

    monkeypatch.setattr(LiteLLMModel, "stream", fake_stream)
    events: list[dict] = []
    model = TracedLiteLLMModel(model_id="groq/openai/gpt-oss-20b", trace=events.append)

    _collect(model, [{"role": "user", "content": [{"text": "hola " * 300}]}])

    assert [e["type"] for e in events] == ["llm_call", "model_attempt"]
    assert events[0]["model"] == "groq/openai/gpt-oss-20b"
    assert events[0]["messages"][0]["content"].endswith("…[truncated]")
    assert events[1]["status"] == "success"


def test_traced_model_reports_failure(monkeypatch):
    async def failing_stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        raise RuntimeError("rate limited")
        yield  # pragma: no cover

    monkeypatch.setattr(LiteLLMModel, "stream", failing_stream)
    events: list[dict] = []
    model = TracedLiteLLMModel(model_id="groq/openai/gpt-oss-120b", trace=events.append)

    with pytest.raises(RuntimeError):
        _collect(model, [{"role": "user", "content": [{"text": "hola"}]}])

    assert events[-1] == {
        "type": "model_attempt",
        "model": "groq/openai/gpt-oss-120b",
        "status": "failed",
        "error": "rate limited",
    }
