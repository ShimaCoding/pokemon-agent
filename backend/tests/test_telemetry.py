"""telemetry: no-op without DD_API_KEY; with it, LLMObs is enabled agentless."""

import asyncio
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

class FakeSpan:
    """Stands in for the ddtrace Span so nothing is sent; records what the error monitor would see."""

    def __init__(self):
        self.error, self.exc, self.finished = 0, None, False

    def set_exc_info(self, exc_type, exc_val, exc_tb):
        self.error, self.exc = 1, exc_type

    def finish(self):
        self.finished = True


@pytest.fixture
def fake_llmobs(monkeypatch):
    from ddtrace.llmobs import LLMObs

    spans: list[FakeSpan] = []
    notes: list[dict] = []

    def agent(**kw):
        spans.append(FakeSpan())
        return spans[-1]

    monkeypatch.setattr(telemetry, "_enabled", True)
    monkeypatch.setattr(LLMObs, "agent", agent)
    monkeypatch.setattr(LLMObs, "annotate", lambda **kw: notes.append(kw))
    return spans, notes


def test_agent_span_marks_errors(fake_llmobs):
    spans, _ = fake_llmobs
    with pytest.raises(ValueError):
        with telemetry.agent_span("conv-12345678") as span:
            raise ValueError("groq down")
    assert span is spans[0]
    assert (span.error, span.exc, span.finished) == (1, ValueError, True)


def test_agent_span_ok(fake_llmobs):
    spans, _ = fake_llmobs
    with telemetry.agent_span("conv-12345678"):
        pass
    assert (spans[0].error, spans[0].finished) == (0, True)


@pytest.mark.parametrize("exc_type", [asyncio.CancelledError, GeneratorExit])
def test_agent_span_ignores_client_disconnect(fake_llmobs, exc_type):
    spans, notes = fake_llmobs
    with pytest.raises(exc_type):
        with telemetry.agent_span("conv-12345678"):
            raise exc_type()
    assert (spans[0].error, spans[0].finished) == (0, True)
    assert notes == [{"span": spans[0], "tags": {"client_disconnected": "true"}}]


def test_agent_span_ignores_aclose(fake_llmobs):
    """Starlette closes the SSE generator with aclose() when the browser goes away."""
    spans, _ = fake_llmobs

    async def stream():
        with telemetry.agent_span("conv-12345678"):
            yield "text"
            yield "more"

    async def main():
        gen = stream()
        assert await gen.asend(None) == "text"
        await gen.aclose()

    asyncio.run(main())
    assert (spans[0].error, spans[0].finished) == (0, True)


def test_record_failed_request(fake_llmobs):
    spans, notes = fake_llmobs
    try:
        raise RuntimeError("mcp down")
    except RuntimeError as exc:
        telemetry.record_failed_request("conv-12345678", exc, input_data="hola")
    [span] = spans
    assert (span.error, span.exc, span.finished) == (1, RuntimeError, True)
    assert notes == [{"span": span, "input_data": "hola", "tags": {"failure_stage": "setup"}}]


def test_record_failed_request_noop_when_disabled(fake_llmobs, monkeypatch):
    spans, _ = fake_llmobs
    monkeypatch.setattr(telemetry, "_enabled", False)
    telemetry.record_failed_request("conv-12345678", RuntimeError("x"))
    assert spans == []


def test_record_failed_request_never_raises(fake_llmobs, monkeypatch):
    from ddtrace.llmobs import LLMObs

    def broken(**kw):
        raise RuntimeError("intake down")

    monkeypatch.setattr(LLMObs, "agent", broken)
    telemetry.record_failed_request("conv-12345678", RuntimeError("x"))


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

def test_eval_helpers_are_noops_when_disabled(monkeypatch):
    from ddtrace.llmobs import LLMObs

    monkeypatch.setattr(LLMObs, "export_span", lambda **kw: pytest.fail("must not export"))
    monkeypatch.setattr(LLMObs, "submit_evaluation", lambda **kw: pytest.fail("must not submit"))
    assert telemetry.export_ids(object()) is None
    assert telemetry.export_ids(None) is None
    telemetry.submit_evaluations({"trace_id": "t", "span_id": "1"}, [object()])


def test_export_ids_and_submit_evaluations(monkeypatch):
    from ddtrace.llmobs import LLMObs

    from backend.evals.checks import CHECKS_VERSION, EvalResult

    monkeypatch.setattr(telemetry, "_enabled", True)
    monkeypatch.setattr(LLMObs, "export_span", lambda span: {"span_id": "123", "trace_id": "ab" * 16, "extra": 1})
    ids = telemetry.export_ids(object())
    assert ids == {"span_id": "123", "trace_id": "ab" * 16}

    calls = []
    monkeypatch.setattr(LLMObs, "submit_evaluation", lambda **kw: calls.append(kw))
    results = [
        EvalResult("pokemon_tool_used", True, "ok", {"tools": ["get_pokedex_entry"]}),
        EvalResult("grounded_facts", False, "x" * 600, {"ungrounded": [99]}),
    ]
    telemetry.submit_evaluations(ids, results)
    assert [c["label"] for c in calls] == ["pokemon_tool_used", "grounded_facts"]
    ok, bad = calls
    assert ok["metric_type"] == "boolean" and ok["value"] is True and ok["assessment"] == "pass"
    assert bad["value"] is False and bad["assessment"] == "fail"
    assert ok["span"] == {"span_id": "123", "trace_id": "ab" * 16}
    assert len(bad["reasoning"]) == 500
    assert bad["metadata"] == {"ungrounded": [99]}
    assert ok["tags"] == {"eval_source": "rules", "checks_version": CHECKS_VERSION}


def test_eval_helpers_swallow_errors(monkeypatch):
    from ddtrace.llmobs import LLMObs

    from backend.evals.checks import EvalResult

    def boom(**kw):
        raise ValueError("boom")

    monkeypatch.setattr(telemetry, "_enabled", True)
    monkeypatch.setattr(LLMObs, "export_span", boom)
    monkeypatch.setattr(LLMObs, "submit_evaluation", boom)
    assert telemetry.export_ids(object()) is None
    telemetry.submit_evaluations({"trace_id": "t", "span_id": "1"}, [EvalResult("dexter_persona", True, "ok", {})])
