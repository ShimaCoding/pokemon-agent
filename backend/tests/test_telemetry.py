"""telemetry: no-op without DD_API_KEY; with it, LLMObs is enabled agentless."""

import pytest

from backend import telemetry


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(telemetry, "_enabled", False)
    for name in ("DD_API_KEY", "DD_SITE", "DD_LLMOBS_ML_APP", *telemetry._DDTRACE_DEFAULTS):
        # setenv first so monkeypatch also undoes what setup_telemetry writes.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_disabled_without_key():
    assert telemetry.setup_telemetry() is False
    assert telemetry.is_enabled() is False


def test_helpers_are_noops_when_disabled():
    with telemetry.agent_span("conv-12345678") as span:
        assert span is None
    with telemetry.llm_span("groq/openai/gpt-oss-20b") as span:
        assert span is None
    tool = telemetry.start_tool_span("get_pokedex_entry")
    assert tool is None
    telemetry.finish_tool_span(tool, input_data={}, output_data="")
    telemetry.annotate(None, input_data="x")


def test_enables_llmobs_agentless(monkeypatch):
    import os

    from ddtrace.llmobs import LLMObs

    calls = []
    monkeypatch.setattr(LLMObs, "enable", lambda **kw: calls.append(kw))
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setenv("DD_SITE", "us5.datadoghq.com")

    assert telemetry.setup_telemetry() is True
    assert telemetry.is_enabled() is True
    [kw] = calls
    assert kw["agentless_enabled"] is True
    assert kw["integrations_enabled"] is False
    assert kw["site"] == "us5.datadoghq.com"
    assert kw["ml_app"] == telemetry.DEFAULT_ML_APP
    assert os.environ["DD_TRACE_ENABLED"] == "false"


def test_streamed_output_collects_text_tools_and_usage():
    from backend.providers import _StreamedOutput

    assert _StreamedOutput().metrics() == {}

    out = _StreamedOutput()
    out.add({"contentBlockDelta": {"delta": {"text": "Pika"}}})
    out.add({"contentBlockStart": {"start": {"toolUse": {"name": "get_pokedex_entry"}}}})
    out.add({"metadata": {"usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12, "cacheReadInputTokens": 8}}})

    assert out.text() == "Pika\n[toolUse get_pokedex_entry]"
    assert out.metrics() == {
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
        "cache_read_input_tokens": 8,
    }


def test_finish_tool_span_forwards_cache_tag(monkeypatch):
    from ddtrace.llmobs import LLMObs

    calls = []
    monkeypatch.setattr(LLMObs, "annotate", lambda **kw: calls.append(kw))
    span = type("Span", (), {"error": 0, "finish": lambda self: None})()

    telemetry.finish_tool_span(span, input_data={"pokemon": "pikachu"}, output_data="ok", tags={"cache": "hit"})
    telemetry.finish_tool_span(span, input_data={}, output_data="ok")

    assert calls[0]["tags"] == {"cache": "hit"}
    assert "tags" not in calls[1]
