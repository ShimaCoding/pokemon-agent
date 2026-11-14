"""Tests for backend/providers.py: model selection and per-attempt tracing."""

import asyncio

import pytest
from strands.models.litellm import LiteLLMModel

from backend import providers
from backend.providers import (
    MODELS_AUTHENTICATED,
    MODELS_PUBLIC,
    TracedLiteLLMModel,
    _effort_from_env,
    build_model,
    call_role,
    model_ids,
)


@pytest.fixture(autouse=True)
def _shipped_effort(monkeypatch):
    # The module reads REASONING_EFFORT_* once at import (env and .env); pin the
    # shipped defaults so a local A/B override does not break these tests.
    monkeypatch.setattr(providers, "REASONING_EFFORT_ROUTING", providers.DEFAULT_REASONING_EFFORT["routing"])
    monkeypatch.setattr(providers, "REASONING_EFFORT_FINAL", providers.DEFAULT_REASONING_EFFORT["final"])


def test_public_requests_get_the_large_model_with_fallback():
    assert model_ids(authenticated=False) == ["groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"]


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
    assert events[0]["call_role"] == "routing"
    assert events[0]["reasoning_effort"] == "low"
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


def _model_event(exception, state=None):
    from types import SimpleNamespace

    return SimpleNamespace(exception=exception, retry=False, invocation_state=state if state is not None else {})


def test_malformed_tool_calls_are_retried_twice():
    from backend.providers import RetryMalformedToolCalls

    hook = RetryMalformedToolCalls()
    groq_error = ValueError("invalid literal for int() with base 10: 'tool_use_failed'")
    state: dict = {}
    retries = []
    for _ in range(3):
        event = _model_event(groq_error, state)
        hook._on_model_result(event)
        retries.append(event.retry)
    assert retries == [True, True, False]


def test_malformed_tool_call_detected_through_cause():
    from backend.providers import RetryMalformedToolCalls

    try:
        try:
            raise RuntimeError("Tool call validation failed: attempted to call tool 'x<|channel|>y'")
        except RuntimeError as inner:
            raise ValueError("wrapped") from inner
    except ValueError as outer:
        assert RetryMalformedToolCalls.is_malformed_tool_call(outer)


def test_other_errors_are_not_retried():
    from backend.providers import RetryMalformedToolCalls

    event = _model_event(RuntimeError("rate limit"))
    RetryMalformedToolCalls()._on_model_result(event)
    assert event.retry is False


# --- reasoning effort per call role ---------------------------------------------

_USER = {"role": "user", "content": [{"text": "háblame de bulbasaur"}]}
_TOOL_USE = {
    "role": "assistant",
    "content": [
        {"reasoningContent": {"reasoningText": {"text": "Need the Pokédex entry."}}},
        {"toolUse": {"toolUseId": "t1", "name": "get_pokedex_entry", "input": {"name": "bulbasaur"}}},
    ],
}
_TOOL_RESULT = {
    "role": "user",
    "content": [{"toolResult": {"toolUseId": "t1", "status": "success", "content": [{"text": "{}"}]}}],
}
_REPLY = {"role": "assistant", "content": [{"text": "BULBASAUR. Pokémon Semilla."}]}
_TURN = [_USER, _TOOL_USE, _TOOL_RESULT]


def test_call_role_turn_opening_user_text_is_routing():
    assert call_role([_USER]) == "routing"
    assert call_role([]) == "routing"


def test_call_role_after_tool_result_is_final():
    assert call_role(_TURN) == "final"
    with_text = {"role": "user", "content": [*_TOOL_RESULT["content"], {"text": "narra"}]}
    assert call_role([_USER, _TOOL_USE, with_text]) == "final"
    assert call_role([_USER, _TOOL_USE]) == "final"


def test_follow_up_turn_is_routing():
    follow_up = {"role": "user", "content": [{"text": "¿y la de charmander?"}]}
    assert call_role([*_TURN, _REPLY, follow_up]) == "routing"


@pytest.mark.parametrize("messages,expected", [([_USER], "low"), (_TURN, "medium")])
def test_format_request_sets_effort_by_role(messages, expected):
    model = TracedLiteLLMModel(model_id="groq/openai/gpt-oss-20b")
    assert model.format_request(messages)["reasoning_effort"] == expected


def test_explicit_params_win():
    model = TracedLiteLLMModel(model_id="groq/openai/gpt-oss-20b", params={"reasoning_effort": "high"})
    assert model.format_request([_USER])["reasoning_effort"] == "high"
    assert model.format_request(_TURN)["reasoning_effort"] == "high"


def test_effort_override_per_model():
    model = TracedLiteLLMModel(
        model_id="groq/openai/gpt-oss-20b", reasoning_effort={"routing": "medium", "final": "low"}
    )
    assert model.format_request([_USER])["reasoning_effort"] == "medium"
    assert model.format_request(_TURN)["reasoning_effort"] == "low"


def test_module_defaults_follow_env_constants(monkeypatch):
    monkeypatch.setattr(providers, "REASONING_EFFORT_ROUTING", "medium")
    monkeypatch.setattr(providers, "REASONING_EFFORT_FINAL", "low")
    model = TracedLiteLLMModel(model_id="groq/openai/gpt-oss-20b")
    assert model.format_request([_USER])["reasoning_effort"] == "medium"
    assert model.format_request(_TURN)["reasoning_effort"] == "low"


@pytest.mark.parametrize(
    "raw,expected",
    [(None, "low"), ("", "low"), ("  ", "low"), ("medium", "medium"), (" HIGH ", "high"), ("minimal", "low")],
)
def test_effort_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("REASONING_EFFORT_TEST", raising=False)
    else:
        monkeypatch.setenv("REASONING_EFFORT_TEST", raw)
    assert _effort_from_env("REASONING_EFFORT_TEST", "low") == expected


def test_shipped_defaults_are_low_routing_medium_final():
    assert providers.DEFAULT_REASONING_EFFORT == {"routing": "low", "final": "medium"}


def test_build_model_candidates_share_effort(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    router = build_model(authenticated=True)
    for messages, expected in (([_USER], "low"), (_TURN, "medium")):
        assert {c.model.format_request(messages)["reasoning_effort"] for c in router.candidates} == {expected}


class _Sent(Exception):
    """Raised by the fake transport once the outgoing request has been captured."""


@pytest.mark.parametrize("model_id", MODELS_AUTHENTICATED)
@pytest.mark.parametrize("messages,expected", [([_USER], "low"), (_TURN, "medium")])
def test_reasoning_effort_reaches_groq_payload(monkeypatch, model_id, messages, expected):
    """End to end through strands -> litellm -> the HTTP body sent to Groq, no network."""
    import json

    import httpx

    sent: list[httpx.Request] = []

    async def fake_send(self, request, *args, **kwargs):
        sent.append(request)
        raise _Sent()

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    model = TracedLiteLLMModel(model_id=model_id)

    with pytest.raises(Exception):  # noqa: B017 - litellm wraps _Sent in its own error type
        _collect(model, messages)

    assert sent, "litellm never built the HTTP request"
    request = sent[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert body["model"] == model_id.removeprefix("groq/")
    assert body["reasoning_effort"] == expected


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("messages", [[_USER], _TURN], ids=["routing", "final"])
def test_every_candidate_streams(monkeypatch, authenticated, messages):
    """Every model the router may pick streams from Groq for real.

    LiteLLM fakes Groq streaming (one response, replayed at the end) whenever
    ``response_format`` is set, which would hold back the whole reply.
    """
    import json

    import httpx

    sent: list[httpx.Request] = []

    async def fake_send(self, request, *args, **kwargs):
        sent.append(request)
        raise _Sent()

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    router = build_model(authenticated)

    for candidate in router.candidates:
        request = candidate.model.format_request(messages)
        assert request["stream"] is True
        assert "response_format" not in request

        sent.clear()
        with pytest.raises(Exception):  # noqa: B017 - litellm wraps _Sent in its own error type
            _collect(candidate.model, messages)
        assert sent, "litellm never built the HTTP request"
        body = json.loads(sent[0].content)
        assert body["stream"] is True
        assert "response_format" not in body


def test_llm_span_tagged_with_effort(monkeypatch):
    from contextlib import nullcontext

    from backend import telemetry

    async def fake_stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        yield {"messageStart": {"role": "assistant"}}

    annotations: list[dict] = []
    monkeypatch.setattr(LiteLLMModel, "stream", fake_stream)
    monkeypatch.setattr(telemetry, "llm_span", lambda model_id: nullcontext("span"))
    monkeypatch.setattr(telemetry, "annotate", lambda span, **fields: annotations.append(fields))

    _collect(TracedLiteLLMModel(model_id="groq/openai/gpt-oss-20b"), _TURN)

    assert annotations[0]["tags"] == {"call_role": "final", "reasoning_effort": "medium"}
    assert annotations[0]["metadata"] == {"call_role": "final", "reasoning_effort": "medium"}
