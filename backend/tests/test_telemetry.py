"""telemetry: no-op without DD_API_KEY; with it, LLMObs is enabled agentless."""

import os

import ddtrace
import pytest

from backend import build_info, telemetry

SHA = "f4bf26b0123456789abcdef0123456789abcdef0"


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(telemetry, "_enabled", False)
    monkeypatch.setattr(ddtrace.config, "version", ddtrace.config.version)
    for name in (
        "DD_API_KEY",
        "DD_SITE",
        "DD_LLMOBS_ML_APP",
        "DD_ENV",
        "DD_VERSION",
        "DD_GIT_COMMIT_SHA",
        "DD_GIT_REPOSITORY_URL",
        *telemetry._DDTRACE_DEFAULTS,
    ):
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


@pytest.fixture
def enable_calls(monkeypatch):
    from ddtrace.llmobs import LLMObs

    calls = []
    monkeypatch.setattr(LLMObs, "enable", lambda **kw: calls.append(kw))
    monkeypatch.setenv("DD_API_KEY", "k")
    return calls


def test_sets_version_and_default_env(monkeypatch, enable_calls):
    monkeypatch.setattr(build_info, "commit_sha", lambda: SHA)

    assert telemetry.setup_telemetry() is True
    [kw] = enable_calls
    assert kw["env"] == telemetry.DEFAULT_ENV == "local"
    assert ddtrace.config.version == SHA[:7]
    assert os.environ["DD_GIT_COMMIT_SHA"] == SHA
    assert os.environ["DD_GIT_REPOSITORY_URL"] == build_info.REPOSITORY_URL


def test_env_and_version_from_environment(monkeypatch, enable_calls):
    monkeypatch.setattr(build_info, "commit_sha", lambda: SHA)
    monkeypatch.setenv("DD_ENV", "prod")
    monkeypatch.setenv("DD_VERSION", "v1")

    telemetry.setup_telemetry()
    [kw] = enable_calls
    assert kw["env"] == "prod"
    assert ddtrace.config.version == "v1"


def test_no_version_leaves_config_untouched(monkeypatch, enable_calls):
    monkeypatch.setattr(build_info, "commit_sha", lambda: None)
    monkeypatch.setattr(ddtrace.config, "version", "untouched")

    telemetry.setup_telemetry()
    assert ddtrace.config.version == "untouched"
    assert "DD_GIT_COMMIT_SHA" not in os.environ


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
