"""telemetry: no-op without backends; DD_API_KEY enables LLMObs agentless, PHOENIX_COLLECTOR_ENDPOINT enables Phoenix."""

import asyncio
import os

import ddtrace
import pytest

from backend import build_info, telemetry

SHA = "f4bf26b0123456789abcdef0123456789abcdef0"


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(telemetry, "_dd_enabled", False)
    monkeypatch.setattr(telemetry, "_phoenix", None)
    monkeypatch.setattr(ddtrace.config, "version", ddtrace.config.version)
    for name in (
        "DD_API_KEY",
        "DD_SITE",
        "DD_LLMOBS_ML_APP",
        "DD_ENV",
        "DD_VERSION",
        "DD_GIT_COMMIT_SHA",
        "DD_GIT_REPOSITORY_URL",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "PHOENIX_API_KEY",
        "PHOENIX_PROJECT_NAME",
        "ADMIN_TRACES_URL",
        *telemetry._DDTRACE_DEFAULTS,
    ):
        # setenv first so monkeypatch also undoes what setup_telemetry writes.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    yield
    telemetry._current_agent.set(None)


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

    def __init__(self, **kw):
        self.kw = kw
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
        spans.append(FakeSpan(**kw))
        return spans[-1]

    monkeypatch.setattr(telemetry, "_dd_enabled", True)
    monkeypatch.setattr(LLMObs, "agent", agent)
    monkeypatch.setattr(LLMObs, "annotate", lambda **kw: notes.append(kw))
    return spans, notes


def test_agent_span_marks_errors(fake_llmobs):
    spans, _ = fake_llmobs
    with pytest.raises(ValueError):
        with telemetry.agent_span("conv-12345678") as span:
            raise ValueError("groq down")
    assert span.dd is spans[0]
    span = span.dd
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
    assert notes == [
        {"span": spans[0], "tags": {"agent_level": "root"}},
        {"span": spans[0], "tags": {"client_disconnected": "true"}},
    ]


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
    assert notes == [
        {"span": span, "tags": {"agent_level": "root"}},
        {"span": span, "input_data": "hola", "tags": {"failure_stage": "setup"}},
    ]


def test_record_failed_request_noop_when_disabled(fake_llmobs, monkeypatch):
    spans, _ = fake_llmobs
    monkeypatch.setattr(telemetry, "_dd_enabled", False)
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

    telemetry.finish_tool_span(
        telemetry._Span(dd=span), input_data={"pokemon": "pikachu"}, output_data="ok", tags={"cache": "hit"}
    )
    telemetry.finish_tool_span(telemetry._Span(dd=span), input_data={}, output_data="ok")

    assert calls[0]["tags"] == {"cache": "hit"}
    assert "tags" not in calls[1]

def test_eval_helpers_are_noops_when_disabled(monkeypatch):
    from ddtrace.llmobs import LLMObs

    monkeypatch.setattr(LLMObs, "export_span", lambda **kw: pytest.fail("must not export"))
    monkeypatch.setattr(LLMObs, "submit_evaluation", lambda **kw: pytest.fail("must not submit"))
    assert telemetry.export_ids(telemetry._Span(dd=object())) is None
    assert telemetry.export_ids(None) is None
    telemetry.submit_evaluations({"trace_id": "t", "span_id": "1"}, [object()])


def test_export_ids_and_submit_evaluations(monkeypatch):
    from ddtrace.llmobs import LLMObs

    from backend.evals.checks import CHECKS_VERSION, EvalResult

    monkeypatch.setattr(telemetry, "_dd_enabled", True)
    monkeypatch.setattr(LLMObs, "export_span", lambda span: {"span_id": "123", "trace_id": "ab" * 16, "extra": 1})
    ids = telemetry.export_ids(telemetry._Span(dd=object()))
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

    monkeypatch.setattr(telemetry, "_dd_enabled", True)
    monkeypatch.setattr(LLMObs, "export_span", boom)
    monkeypatch.setattr(LLMObs, "submit_evaluation", boom)
    assert telemetry.export_ids(telemetry._Span(dd=object())) is None
    telemetry.submit_evaluations({"trace_id": "t", "span_id": "1"}, [EvalResult("dexter_persona", True, "ok", {})])


def test_submit_feedback(monkeypatch):
    from ddtrace.llmobs import LLMObs

    calls = []
    monkeypatch.setattr(LLMObs, "submit_feedback", lambda **kw: calls.append(kw))
    ids = {"span_id": "123", "trace_id": "ab" * 16}
    telemetry.submit_feedback(ids, value="up", submitter_id="conv-12345678")
    assert calls == []  # disabled → no-op

    monkeypatch.setattr(telemetry, "_dd_enabled", True)
    telemetry.submit_feedback(ids, value="down", submitter_id="conv-12345678")
    [kw] = calls
    assert kw["label"] == "user_feedback"
    assert (kw["metric_type"], kw["value"], kw["assessment"]) == ("categorical", "thumbs_down", "fail")
    assert kw["submitter"] == {"id": "conv-12345678", "type": "user"}
    assert kw["span"] == ids

    monkeypatch.setattr(LLMObs, "submit_feedback", lambda **kw: 1 / 0)
    telemetry.submit_feedback(ids, value="up", submitter_id="conv-12345678")  # swallowed


def test_streamed_output_collects_structured_tool_calls():
    from backend.providers import _StreamedOutput, _tool_definitions

    out = _StreamedOutput()
    out.add({"contentBlockStart": {"start": {"toolUse": {"name": "get_pokedex_entry", "toolUseId": "t1"}}}})
    out.add({"contentBlockDelta": {"delta": {"toolUse": {"input": '{"pokemon": '}}}})
    out.add({"contentBlockDelta": {"delta": {"toolUse": {"input": '"pikachu"}'}}}})
    out.add({"contentBlockStart": {"start": {"toolUse": {"name": "broken", "toolUseId": "t2"}}}})
    out.add({"contentBlockDelta": {"delta": {"toolUse": {"input": "{not json"}}}})

    assert out.tool_calls() == [
        {"name": "get_pokedex_entry", "arguments": {"pokemon": "pikachu"}, "tool_id": "t1", "type": "function"},
        {"name": "broken", "arguments": {"raw": "{not json"}, "tool_id": "t2", "type": "function"},
    ]
    spec = {"name": "get_pokedex_entry", "description": "Entry", "inputSchema": {"json": {"type": "object"}}}
    assert _tool_definitions([spec]) == [{"name": "get_pokedex_entry", "description": "Entry", "schema": {"type": "object"}}]
    assert _tool_definitions(None) == []


# ---------------------------------------------------------------------------
# Phoenix
# ---------------------------------------------------------------------------

import json  # noqa: E402
import re  # noqa: E402

import httpx  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import StatusCode  # noqa: E402

from backend import telemetry_phoenix  # noqa: E402
from backend.main import FeedbackRequest  # noqa: E402


class SyncExecutor:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture
def px(monkeypatch):
    """Phoenix backend writing to memory; annotation POSTs are recorded instead of sent."""
    exporter = InMemorySpanExporter()
    posts: list[httpx.Request] = []

    def handler(request):
        posts.append(request)
        return httpx.Response(200, json={"data": []})

    backend = telemetry_phoenix.PhoenixBackend(
        endpoint="http://phoenix:6006/",
        api_key="secret",
        project="mcpokedex-test",
        env="test",
        version="abc1234",
        processor=SimpleSpanProcessor(exporter),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        executor=SyncExecutor(),
    )
    monkeypatch.setattr(telemetry, "_phoenix", backend)
    yield backend, exporter, posts
    backend.shutdown()


def _by_name(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


def _metadata(span):
    return json.loads(span.attributes["metadata"])


def test_phoenix_disabled_without_endpoint():
    assert telemetry.setup_telemetry() is False
    assert telemetry._phoenix is None


def test_phoenix_enabled_by_endpoint(monkeypatch):
    monkeypatch.setattr(build_info, "commit_sha", lambda: SHA)
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006/v1/traces")
    monkeypatch.setenv("DD_ENV", "prod")

    assert telemetry.setup_telemetry() is True
    assert telemetry.is_enabled() and not telemetry._dd_enabled
    backend = telemetry._phoenix
    assert (backend.base_url, backend.project) == ("http://phoenix:6006", "mcpokedex-prod")
    backend.shutdown()


def test_phoenix_project_override_and_both_backends(monkeypatch, enable_calls):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006")
    monkeypatch.setenv("PHOENIX_PROJECT_NAME", "custom")

    assert telemetry.setup_telemetry() is True
    assert telemetry._dd_enabled and len(enable_calls) == 1
    assert telemetry._phoenix.project == "custom"
    assert telemetry._phoenix.admin_traces_url == ""
    telemetry._phoenix.shutdown()


def test_phoenix_admin_traces_url_from_environment(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006")
    monkeypatch.setenv("ADMIN_TRACES_URL", " https://app.test/admin/traces ")

    assert telemetry.setup_telemetry() is True
    assert telemetry._phoenix.admin_traces_url == "https://app.test/admin/traces"
    telemetry._phoenix.shutdown()


def test_phoenix_trace_tree(px):
    backend, exporter, _ = px
    with telemetry.agent_span("conv-12345678") as agent:
        with telemetry.llm_span("groq/openai/gpt-oss-20b") as llm:
            telemetry.annotate(
                llm,
                input_data=[{"role": "system", "content": "Eres Dexter"}, {"role": "user", "content": "pikachu"}],
                output_data=[
                    {
                        "role": "assistant",
                        "content": "[toolUse get_pokedex_entry]",
                        "tool_calls": [{"name": "get_pokedex_entry", "arguments": {"pokemon": "pikachu"}, "tool_id": "t1"}],
                    }
                ],
                tool_definitions=[{"name": "get_pokedex_entry", "description": "Entry", "schema": {"type": "object"}}],
                metrics={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cache_read_input_tokens": 64},
                metadata={"call_role": "routing"},
                tags={"reasoning_effort": "low"},
            )
        tool = telemetry.start_tool_span("get_pokedex_entry")
        telemetry.finish_tool_span(tool, input_data={"pokemon": "pikachu"}, output_data="Pikachu…", tags={"cache": "hit"})
        telemetry.annotate(agent, input_data="pikachu", output_data="¡Pikachu!", metrics={"total_tokens": 120})

    spans = _by_name(exporter)
    a, l, t = spans["dexter"], spans["chat"], spans["get_pokedex_entry"]
    assert a.parent is None
    assert l.parent.span_id == t.parent.span_id == a.context.span_id
    assert {s.attributes["openinference.span.kind"] for s in (a, l, t)} == {"AGENT", "LLM", "TOOL"}
    assert {s.attributes["session.id"] for s in (a, l, t)} == {"conv-12345678"}

    assert l.attributes["llm.model_name"] == "openai/gpt-oss-20b"
    assert l.attributes["llm.provider"] == "groq"
    assert l.attributes["llm.token_count.prompt"] == 100
    assert l.attributes["llm.token_count.completion"] == 20
    assert l.attributes["llm.token_count.total"] == 120
    assert l.attributes["llm.token_count.prompt_details.cache_read"] == 64
    assert l.attributes["llm.input_messages.1.message.content"] == "pikachu"
    assert l.attributes["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"] == "get_pokedex_entry"
    assert json.loads(l.attributes["llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments"]) == {
        "pokemon": "pikachu"
    }
    assert json.loads(l.attributes["llm.tools.0.tool.json_schema"])["function"]["name"] == "get_pokedex_entry"
    assert _metadata(l) == {"env": "test", "version": "abc1234", "call_role": "routing", "reasoning_effort": "low"}

    assert t.attributes["tool.name"] == "get_pokedex_entry"
    assert json.loads(t.attributes["input.value"]) == {"pokemon": "pikachu"}
    assert t.attributes["output.value"] == "Pikachu…"
    assert _metadata(t)["cache"] == "hit"

    # The agent span never carries token counts: Phoenix sums its llm children.
    assert not [k for k in a.attributes if k.startswith("llm.token_count")]
    assert _metadata(a)["total_tokens"] == 120
    assert (a.attributes["input.value"], a.attributes["output.value"]) == ("pikachu", "¡Pikachu!")
    assert a.resource.attributes["openinference.project.name"] == "mcpokedex-test"
    assert all(s.status.status_code == StatusCode.UNSET for s in (a, l, t))
    assert telemetry._current_agent.get() is None


def test_phoenix_root_ignores_foreign_otel_context(px):
    """A FastAPI/Strands span of the global provider must not become the agent's parent."""
    from opentelemetry.sdk.trace import TracerProvider

    _, exporter, _ = px
    with TracerProvider().get_tracer("other").start_as_current_span("POST /api/agent/run"):
        with telemetry.agent_span("conv-12345678"):
            pass
    assert _by_name(exporter)["dexter"].parent is None


def test_phoenix_errors(px):
    _, exporter, _ = px
    with pytest.raises(ValueError):
        with telemetry.agent_span("conv-12345678"):
            with telemetry.llm_span("groq/openai/gpt-oss-20b"):
                raise ValueError("groq down")
    tool = telemetry.start_tool_span("get_pokedex_entry")
    telemetry.finish_tool_span(tool, input_data={}, output_data="boom", error=True)

    spans = _by_name(exporter)
    assert spans["dexter"].status.status_code == StatusCode.ERROR
    assert spans["chat"].status.status_code == StatusCode.ERROR
    assert spans["get_pokedex_entry"].status.status_code == StatusCode.ERROR
    assert spans["dexter"].events[0].name == "exception"


@pytest.mark.parametrize("exc_type", [asyncio.CancelledError, GeneratorExit])
def test_phoenix_client_disconnect_is_not_an_error(px, exc_type):
    _, exporter, _ = px
    with pytest.raises(exc_type):
        with telemetry.agent_span("conv-12345678"):
            raise exc_type()
    [span] = exporter.get_finished_spans()
    assert span.status.status_code == StatusCode.UNSET
    assert _metadata(span)["client_disconnected"] == "true"


def test_phoenix_agent_span_survives_aclose(px):
    """Starlette closes the SSE generator with aclose() when the browser goes away."""
    _, exporter, _ = px

    async def stream():
        with telemetry.agent_span("conv-12345678"):
            yield "text"
            yield "more"

    async def main():
        gen = stream()
        assert await gen.asend(None) == "text"
        await gen.aclose()

    asyncio.run(main())
    [span] = exporter.get_finished_spans()
    assert span.status.status_code == StatusCode.UNSET


def test_phoenix_record_failed_request(px):
    _, exporter, _ = px
    try:
        raise RuntimeError("mcp down")
    except RuntimeError as exc:
        telemetry.record_failed_request("conv-12345678", exc, input_data="hola")
    [span] = exporter.get_finished_spans()
    assert span.attributes["openinference.span.kind"] == "AGENT"
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["input.value"] == "hola"
    assert _metadata(span)["failure_stage"] == "setup"


def test_phoenix_ids_feedback_and_evals(px):
    from backend.evals.checks import CHECKS_VERSION, EvalResult

    _, exporter, posts = px
    with telemetry.agent_span("conv-12345678") as agent:
        ids = telemetry.export_ids(agent)
    # Same contract as Datadog's ids: the frontend sends them back to /api/feedback.
    FeedbackRequest(value="up", **ids)
    span_hex = format(exporter.get_finished_spans()[0].context.span_id, "016x")
    assert format(int(ids["span_id"]), "016x") == span_hex

    telemetry.submit_evaluations(ids, [EvalResult("grounded_facts", False, "x" * 600, {"ungrounded": [99]})])
    telemetry.submit_feedback(ids, value="down", submitter_id="conv-12345678")

    evals, feedback = (json.loads(p.content)["data"] for p in posts)
    assert all(str(p.url) == "http://phoenix:6006/v1/span_annotations?sync=false" for p in posts)
    assert all(p.headers["authorization"] == "Bearer secret" for p in posts)
    assert evals == [
        {
            "span_id": span_hex,
            "name": "grounded_facts",
            "annotator_kind": "CODE",
            "result": {"label": "fail", "score": 0.0, "explanation": "x" * 500},
            "metadata": {"ungrounded": [99], "eval_source": "rules", "checks_version": CHECKS_VERSION},
        }
    ]
    assert feedback == [
        {
            "span_id": span_hex,
            "name": "user_feedback",
            "annotator_kind": "HUMAN",
            "result": {"label": "thumbs_down", "score": 0.0},
            "identifier": "conv-12345678",
            "metadata": {"source": "frontend", "submitter_id": "conv-12345678"},
        }
    ]


def test_phoenix_unknown_ids_send_nothing(px):
    _, _, posts = px
    telemetry.submit_feedback({"span_id": "123", "trace_id": "ab" * 16}, value="up", submitter_id="conv-12345678")
    assert posts == []


def test_both_backends_share_datadog_ids(px, fake_llmobs, monkeypatch):
    """With both on, the frontend gets Datadog's ids and Phoenix maps them to its own span."""
    from ddtrace.llmobs import LLMObs

    _, exporter, posts = px
    dd_spans, _ = fake_llmobs
    monkeypatch.setattr(LLMObs, "export_span", lambda span: {"span_id": "123", "trace_id": "ab" * 16})
    sent = []
    monkeypatch.setattr(LLMObs, "submit_feedback", lambda **kw: sent.append(kw))

    with telemetry.agent_span("conv-12345678") as agent:
        ids = telemetry.export_ids(agent)
    assert agent.dd is dd_spans[0] and dd_spans[0].finished
    assert ids == {"span_id": "123", "trace_id": "ab" * 16}

    telemetry.submit_feedback(ids, value="up", submitter_id="conv-12345678")
    assert sent[0]["span"] == ids
    [post] = posts
    span_hex = format(exporter.get_finished_spans()[0].context.span_id, "016x")
    assert json.loads(post.content)["data"][0]["span_id"] == span_hex


def test_phoenix_failures_never_break_a_request(px, monkeypatch):
    backend, _, _ = px

    def boom(*a, **kw):
        raise RuntimeError("otel broke")

    for name in ("start_agent", "start_llm", "start_tool", "annotate", "finish", "export_ids"):
        monkeypatch.setattr(backend, name, boom)
    with telemetry.agent_span("conv-12345678") as agent:
        with telemetry.llm_span("groq/openai/gpt-oss-20b") as llm:
            telemetry.annotate(llm, input_data="x")
        telemetry.finish_tool_span(telemetry.start_tool_span("t"), input_data={}, output_data="")
        assert telemetry.export_ids(agent) is None


def test_phoenix_annotation_errors_are_swallowed(px):
    backend, exporter, _ = px
    backend._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with telemetry.agent_span("conv-12345678") as agent:
        ids = telemetry.export_ids(agent)
    telemetry.submit_feedback(ids, value="up", submitter_id="conv-12345678")
    assert re.fullmatch(r"[0-9a-f]{32}", ids["trace_id"])


# ---------------------------------------------------------------------------
# Multi-agent: sub-agent spans nest under the agent open in the current context
# ---------------------------------------------------------------------------

import contextvars  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

MODEL = "groq/openai/gpt-oss-20b"


def _tree(exporter) -> dict[str, list[str]]:
    """Span name → names of its children in start order; the root is under ``None``."""
    spans = sorted(exporter.get_finished_spans(), key=lambda s: s.start_time)
    names = {s.context.span_id: s.name for s in spans}
    tree: dict = {}
    for s in spans:
        tree.setdefault(names[s.parent.span_id] if s.parent else None, []).append(s.name)
    return tree


def _parent_name(exporter, span) -> str | None:
    names = {s.context.span_id: s.name for s in exporter.get_finished_spans()}
    return names[span.parent.span_id] if span.parent else None


def test_sub_agent_nests_under_the_open_agent(px):
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678") as root:
        with telemetry.llm_span(MODEL):
            pass
        with telemetry.sub_agent_span("researcher") as sub:
            assert telemetry._current_agent.get() is sub
            with telemetry.llm_span(MODEL):
                pass
            telemetry.finish_tool_span(telemetry.start_tool_span("search_pokemon"), input_data={}, output_data="ok")
        assert telemetry._current_agent.get() is root
        telemetry.finish_tool_span(telemetry.start_tool_span("get_pokedex_entry"), input_data={}, output_data="ok")
    assert telemetry._current_agent.get() is None

    assert _tree(exporter) == {
        None: ["dexter"],
        "dexter": ["chat", "researcher", "get_pokedex_entry"],
        "researcher": ["chat", "search_pokemon"],
    }
    spans = exporter.get_finished_spans()
    assert len({s.context.trace_id for s in spans}) == 1
    assert {s.attributes["session.id"] for s in spans} == {"conv-12345678"}


def test_agent_spans_carry_graph_attributes(px):
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678"):
        with telemetry.sub_agent_span("researcher") as sub:
            with telemetry.llm_span(MODEL) as llm:
                telemetry.annotate(llm, metrics={"total_tokens": 7})
            telemetry.finish_tool_span(telemetry.start_tool_span("search_pokemon"), input_data={}, output_data="ok")
            telemetry.annotate(sub, input_data="busca a mew", output_data="MEW", metrics={"total_tokens": 7})

    spans = _by_name(exporter)
    root, sub = spans["dexter"], spans["researcher"]
    for span, name in ((root, "dexter"), (sub, "researcher")):
        assert span.attributes["openinference.span.kind"] == "AGENT"
        assert span.attributes["agent.name"] == span.attributes["graph.node.id"] == name
        assert span.attributes["graph.node.name"] == name
        # Tokens stay on the llm spans: Phoenix sums each agent's subtree itself.
        assert not [k for k in span.attributes if k.startswith("llm.token_count")]
    assert "graph.node.parent_id" not in root.attributes
    assert sub.attributes["graph.node.parent_id"] == "dexter"
    assert (_metadata(root)["agent_level"], _metadata(sub)["agent_level"]) == ("root", "sub")
    assert _metadata(sub)["total_tokens"] == 7
    assert (sub.attributes["input.value"], sub.attributes["output.value"]) == ("busca a mew", "MEW")
    for span in (spans["chat"], spans["search_pokemon"]):
        assert not [k for k in span.attributes if k.startswith(("graph.", "agent."))]
        assert "agent_level" not in _metadata(span)


def test_root_agent_span_links_to_the_admin_run_view(px, monkeypatch):
    backend, exporter, _ = px
    monkeypatch.setattr(backend, "admin_traces_url", "https://app.test/admin/traces")
    with telemetry.agent_span("conv-12345678") as root:
        with telemetry.sub_agent_span("researcher"):
            with telemetry.llm_span(MODEL):
                pass
            telemetry.finish_tool_span(telemetry.start_tool_span("search_pokemon"), input_data={}, output_data="ok")
        telemetry.annotate(root, tags={"guard": "none"})  # later metadata keeps the link
        trace_id = telemetry.export_ids(root)["trace_id"]

    spans = _by_name(exporter)
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert _metadata(spans["dexter"])["admin_url"] == f"https://app.test/admin/traces?trace={trace_id}"
    assert _metadata(spans["dexter"])["guard"] == "none"
    for name in ("researcher", "chat", "search_pokemon"):
        assert "admin_url" not in _metadata(spans[name])


def test_no_admin_link_without_the_run_view_url(px):
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678"):
        with telemetry.sub_agent_span("researcher"):
            pass
    telemetry.record_failed_request("conv-12345678", RuntimeError("mcp down"))

    assert len(exporter.get_finished_spans()) == 3
    assert not [s.name for s in exporter.get_finished_spans() if "admin_url" in _metadata(s)]


def test_admin_link_keeps_a_query_already_in_the_url(px, monkeypatch):
    backend, exporter, _ = px
    monkeypatch.setattr(backend, "admin_traces_url", "https://app.test/admin/traces?env=prod")
    telemetry.record_failed_request("conv-12345678", RuntimeError("mcp down"))  # a root agent span too

    url = _metadata(_by_name(exporter)["dexter"])["admin_url"]
    assert re.fullmatch(r"https://app\.test/admin/traces\?env=prod&trace=[0-9a-f]{32}", url)


def test_root_agent_name_is_configurable(px):
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678", name="orquestador"):
        with telemetry.sub_agent_span("researcher"):
            pass
    spans = _by_name(exporter)
    assert spans["orquestador"].attributes["agent.name"] == "orquestador"
    assert spans["researcher"].attributes["graph.node.parent_id"] == "orquestador"
    assert telemetry.DEFAULT_AGENT_NAME == "dexter"


def test_sub_agents_nest_two_levels_deep(px):
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678"):
        with telemetry.sub_agent_span("planner"):
            with telemetry.sub_agent_span("researcher"):
                with telemetry.llm_span(MODEL):
                    pass
            with telemetry.llm_span(MODEL):
                pass
    assert _tree(exporter) == {
        None: ["dexter"],
        "dexter": ["planner"],
        "planner": ["researcher", "chat"],
        "researcher": ["chat"],
    }
    assert _by_name(exporter)["researcher"].attributes["graph.node.parent_id"] == "planner"


def test_parallel_sub_agents_keep_their_own_children(px):
    """Graph nodes and concurrent tools run as asyncio tasks: each has its own copy of the context."""
    _, exporter, _ = px

    async def node(name: str, tool: str, started: asyncio.Event, other: asyncio.Event):
        with telemetry.sub_agent_span(name):
            started.set()
            await other.wait()  # both sub-agents are open before either starts a child
            telemetry.finish_tool_span(telemetry.start_tool_span(tool), input_data={}, output_data="ok")

    async def main():
        a, b = asyncio.Event(), asyncio.Event()
        with telemetry.agent_span("conv-12345678") as root:
            await asyncio.gather(node("alpha", "tool_a", a, b), node("beta", "tool_b", b, a))
            assert telemetry._current_agent.get() is root
            with telemetry.llm_span(MODEL):
                pass

    asyncio.run(main())
    tree = _tree(exporter)
    assert sorted(tree["dexter"][:2]) == ["alpha", "beta"] and tree["dexter"][2] == "chat"
    assert (tree["alpha"], tree["beta"]) == (["tool_a"], ["tool_b"])


def test_threads_that_copy_the_context_keep_the_agent(px):
    _, exporter, _ = px

    def blocking_model_call():
        with telemetry.llm_span(MODEL):
            pass

    async def main():
        with telemetry.agent_span("conv-12345678"):
            with telemetry.sub_agent_span("researcher"):
                await asyncio.to_thread(blocking_model_call)

    asyncio.run(main())
    assert _tree(exporter)["researcher"] == ["chat"]


def test_sub_agent_closed_from_another_context(px):
    """Like the SSE generator closed by Starlette: the context that opened the span never resets it."""
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678") as root:
        sub = telemetry.start_sub_agent_span("researcher")
        contextvars.copy_context().run(telemetry.finish_sub_agent_span, sub)
        assert telemetry._current_agent.get() is sub  # stale here, skipped by _open_agent
        with telemetry.llm_span(MODEL):
            pass
        with telemetry.sub_agent_span("writer"):
            pass
        assert telemetry._current_agent.get() is root
    assert telemetry._current_agent.get() is None
    assert _tree(exporter) == {None: ["dexter"], "dexter": ["researcher", "chat", "writer"]}
    assert _by_name(exporter)["writer"].attributes["graph.node.parent_id"] == "dexter"


def test_root_closed_from_another_context_leaves_no_agent_open(px):
    _, exporter, _ = px

    def close(cm):
        cm.__exit__(None, None, None)

    cm = telemetry.agent_span("conv-12345678")
    cm.__enter__()
    contextvars.copy_context().run(close, cm)
    assert telemetry.start_sub_agent_span("researcher") is None
    telemetry.finish_tool_span(telemetry.start_tool_span("search_pokemon"), input_data={}, output_data="ok")
    assert _by_name(exporter)["search_pokemon"].parent is None


def test_sub_agent_without_an_open_agent_has_no_span(px):
    _, exporter, _ = px
    assert telemetry.start_sub_agent_span("researcher") is None
    telemetry.finish_sub_agent_span(None, output_data="x")
    with telemetry.sub_agent_span("researcher") as sub:
        assert sub is None
    with telemetry.agent_span("conv-12345678"):
        pass
    assert telemetry.start_sub_agent_span("researcher") is None  # the request is over
    assert [s.name for s in exporter.get_finished_spans()] == ["dexter"]


def test_sub_agent_helpers_are_noops_when_disabled():
    assert telemetry.start_sub_agent_span("researcher") is None
    with telemetry.sub_agent_span("researcher") as sub:
        assert sub is None
    assert telemetry._current_agent.get() is None


def test_sub_agent_error_marks_only_the_sub_agent(px):
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678"):
        with pytest.raises(ValueError):
            with telemetry.sub_agent_span("researcher"):
                raise ValueError("pokeapi down")
        with pytest.raises(asyncio.CancelledError):
            with telemetry.sub_agent_span("writer"):
                raise asyncio.CancelledError()
        failed = telemetry.start_sub_agent_span("critic")
        telemetry.finish_sub_agent_span(failed, error=True)
    spans = _by_name(exporter)
    assert spans["researcher"].status.status_code == StatusCode.ERROR
    assert spans["researcher"].events[0].name == "exception"
    assert spans["critic"].status.status_code == StatusCode.ERROR
    assert spans["writer"].status.status_code == StatusCode.UNSET
    assert spans["dexter"].status.status_code == StatusCode.UNSET  # it handled the failures


def test_sub_agent_error_that_propagates_marks_the_root_too(px):
    _, exporter, _ = px
    with pytest.raises(ValueError):
        with telemetry.agent_span("conv-12345678"):
            with telemetry.sub_agent_span("researcher"):
                raise ValueError("pokeapi down")
    spans = _by_name(exporter)
    assert {spans[n].status.status_code for n in ("dexter", "researcher")} == {StatusCode.ERROR}
    assert telemetry._current_agent.get() is None


def test_ids_evals_and_feedback_stay_on_the_root_agent(px):
    from backend.evals.checks import EvalResult

    _, exporter, posts = px
    with telemetry.agent_span("conv-12345678") as root:
        with telemetry.sub_agent_span("researcher"):
            ids = telemetry.export_ids(root)
    telemetry.submit_evaluations(ids, [EvalResult("grounded_facts", True, "ok", {})])
    telemetry.submit_feedback(ids, value="up", submitter_id="conv-12345678")

    root_hex = format(_by_name(exporter)["dexter"].context.span_id, "016x")
    assert format(int(ids["span_id"]), "016x") == root_hex
    assert [json.loads(p.content)["data"][0]["span_id"] for p in posts] == [root_hex, root_hex]


def test_datadog_sub_agent_takes_name_and_no_session(fake_llmobs):
    spans, notes = fake_llmobs
    with telemetry.agent_span("conv-12345678", name="orquestador"):
        with telemetry.sub_agent_span("researcher"):
            pass
        failed = telemetry.start_sub_agent_span("critic")
        telemetry.finish_sub_agent_span(failed, output_data="nada", metrics={"total_tokens": 9}, error=True)

    root, sub, failed = spans
    # ddtrace nests the sub-agent under its active span and LLM Obs inherits the session.
    assert root.kw == {"name": "orquestador", "session_id": "conv-12345678"}
    assert sub.kw == {"name": "researcher"} and failed.kw == {"name": "critic"}
    assert all(s.finished for s in spans)
    assert (root.error, sub.error, failed.error) == (0, 0, 1)
    assert notes == [
        {"span": root, "tags": {"agent_level": "root"}},
        {"span": sub, "tags": {"agent_level": "sub"}},
        {"span": failed, "tags": {"agent_level": "sub"}},
        {"span": failed, "output_data": "nada", "metrics": {"total_tokens": 9}},
    ]


def test_datadog_sub_agent_without_an_open_agent_has_no_span(fake_llmobs):
    spans, _ = fake_llmobs
    assert telemetry.start_sub_agent_span("researcher") is None
    assert spans == []


def test_sub_agent_span_never_raises(fake_llmobs, monkeypatch):
    from ddtrace.llmobs import LLMObs

    spans, _ = fake_llmobs
    with telemetry.agent_span("conv-12345678") as root:
        monkeypatch.setattr(LLMObs, "agent", lambda **kw: 1 / 0)
        assert telemetry.start_sub_agent_span("researcher") is None
        assert telemetry._current_agent.get() is root


# --- Real Strands agents: pins that invocation hooks run in the agent's own context ---


def _reply(text: str) -> list[dict]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": text}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12}, "metrics": {"latencyMs": 1}}},
    ]


def _tool_call(name: str, arguments: dict) -> list[dict]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": f"call-{name}"}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]


@pytest.fixture
def scripted_model(monkeypatch):
    """TracedLiteLLMModel (the real llm spans) over a model scripted per agent, keyed by system prompt."""
    from strands.models.litellm import LiteLLMModel

    from backend.providers import TracedLiteLLMModel

    scripts: dict[str, callable] = {}

    async def fake_stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        for event in scripts[system_prompt](messages):
            await asyncio.sleep(0)  # let parallel agents interleave
            yield event

    monkeypatch.setattr(LiteLLMModel, "stream", fake_stream)

    def build(system_prompt: str, script) -> TracedLiteLLMModel:
        scripts[system_prompt] = script
        return TracedLiteLLMModel(model_id=MODEL)

    return build


def _has_tool_result(messages) -> bool:
    return any("toolResult" in block for message in messages for block in message["content"])


def test_strands_agent_as_tool_nests_under_the_calling_agent(px, scripted_model):
    from strands import Agent

    from backend import main

    _, exporter, _ = px
    researcher = Agent(
        name="researcher",
        model=scripted_model("researcher", lambda messages: _reply("Mew pesa 4 kg.")),
        system_prompt="researcher",
        hooks=[telemetry.SubAgentTrace()],
        callback_handler=None,
    )
    events: list[dict] = []
    dexter = Agent(
        name="dexter",
        model=scripted_model(
            "dexter",
            lambda messages: _reply("MEW. 4 kg.")
            if _has_tool_result(messages)
            else _tool_call("researcher", {"input": "¿cuánto pesa mew?"}),
        ),
        system_prompt="dexter",
        tools=[researcher.as_tool()],
        hooks=[main._ToolTrace(events.append)],
        callback_handler=None,
    )

    async def run():
        with telemetry.agent_span("conv-12345678") as root:
            result = await dexter.invoke_async("¿cuánto pesa mew?")
            assert telemetry._current_agent.get() is root
            return str(result)

    assert asyncio.run(run()).strip() == "MEW. 4 kg."

    # No TOOL span "researcher": the sub-agent span stands for the call.
    assert _tree(exporter) == {None: ["dexter"], "dexter": ["chat", "researcher", "chat"], "researcher": ["chat"]}
    kinds = [s.attributes["openinference.span.kind"] for s in exporter.get_finished_spans()]
    assert sorted(kinds) == ["AGENT", "AGENT", "LLM", "LLM", "LLM"]
    sub = _by_name(exporter)["researcher"]
    assert sub.attributes["graph.node.parent_id"] == "dexter"
    assert (sub.attributes["input.value"], sub.attributes["output.value"]) == ("¿cuánto pesa mew?", "Mew pesa 4 kg.")
    assert _metadata(sub)["total_tokens"] == 12
    assert sub.status.status_code == StatusCode.UNSET
    # The SSE events of the call are still emitted.
    assert [(e["type"], e["tool"]) for e in events] == [("tool_call", "researcher"), ("tool_result", "researcher")]
    assert "Mew pesa 4 kg." in events[1]["result"]


def test_strands_sub_agent_failure_marks_its_span(px, scripted_model):
    from strands import Agent

    _, exporter, _ = px

    def broken(messages):
        raise RuntimeError("groq down")

    researcher = Agent(
        name="researcher",
        model=scripted_model("researcher", broken),
        system_prompt="researcher",
        hooks=[telemetry.SubAgentTrace()],
        callback_handler=None,
    )

    async def run():
        with telemetry.agent_span("conv-12345678") as root:
            with pytest.raises(RuntimeError):
                await researcher.invoke_async("mew")
            assert telemetry._current_agent.get() is root

    asyncio.run(run())
    spans = _by_name(exporter)
    assert spans["researcher"].status.status_code == StatusCode.ERROR
    assert spans["researcher"].status.description == "RuntimeError: groq down"
    assert spans["researcher"].events[0].name == "exception"
    assert spans["dexter"].status.status_code == StatusCode.UNSET
    assert "output.value" not in spans["researcher"].attributes


def test_strands_structured_output_without_a_result_is_not_an_error(px, scripted_model, monkeypatch):
    """Agent.structured_output fires AfterInvocationEvent with result=None on success too."""
    from pydantic import BaseModel
    from strands import Agent
    from strands.models.litellm import LiteLLMModel

    class Weight(BaseModel):
        kg: float

    outcome: dict = {}

    async def fake_structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        if "error" in outcome:
            raise outcome["error"]
        yield {"output": output_model(kg=4.0)}

    monkeypatch.setattr(LiteLLMModel, "structured_output", fake_structured_output)
    _, exporter, _ = px
    researcher = Agent(
        name="researcher",
        model=scripted_model("researcher", lambda messages: _reply("unused")),
        system_prompt="researcher",
        hooks=[telemetry.SubAgentTrace()],
        callback_handler=None,
    )

    async def run():
        with telemetry.agent_span("conv-12345678"):
            with pytest.warns(DeprecationWarning):
                assert (await researcher.structured_output_async(Weight, "¿cuánto pesa mew?")).kg == 4.0
            outcome["error"] = RuntimeError("groq down")
            with pytest.warns(DeprecationWarning), pytest.raises(RuntimeError):
                await researcher.structured_output_async(Weight, "¿cuánto pesa mew?")

    asyncio.run(run())
    ok, failed = [s for s in exporter.get_finished_spans() if s.name == "researcher"]
    assert ok.status.status_code == StatusCode.UNSET
    assert failed.status.status_code == StatusCode.ERROR
    assert _by_name(exporter)["dexter"].status.status_code == StatusCode.UNSET


def _invocation(result=None):
    from types import SimpleNamespace

    return SimpleNamespace(agent=SimpleNamespace(name="researcher"), messages=[], result=result)


def _run_hook(px, body) -> StatusCode:
    """Run ``body(hook)`` under a root agent and return the status of the sub-agent span."""
    _, exporter, _ = px
    with telemetry.agent_span("conv-12345678"):
        body(telemetry.SubAgentTrace())
    return _by_name(exporter)["researcher"].status.status_code


def test_sub_agent_trace_no_result_alone_is_not_an_error(px):
    # A run a hook denied: no result, nothing raised.
    def denied(hook):
        hook._on_start(_invocation())
        hook._on_end(_invocation())

    assert _run_hook(px, denied) == StatusCode.UNSET


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ValueError("pokeapi down"), StatusCode.ERROR),
        (asyncio.CancelledError(), StatusCode.UNSET),
        (GeneratorExit(), StatusCode.UNSET),
    ],
)
def test_sub_agent_trace_marks_what_the_run_raised(px, exc, expected):
    # Strands fires the After event from a ``finally``, while the exception propagates.
    def raising(hook):
        with pytest.raises(type(exc)):
            hook._on_start(_invocation())
            try:
                raise exc
            finally:
                hook._on_end(_invocation())

    assert _run_hook(px, raising) == expected


def test_sub_agent_trace_ignores_the_exception_its_caller_is_handling(px):
    # A caller that retries inside ``except``: that exception is not this run's.
    def retry(hook):
        try:
            raise ValueError("first attempt")
        except ValueError:
            hook._on_start(_invocation())
            hook._on_end(_invocation())

    assert _run_hook(px, retry) == StatusCode.UNSET


def test_strands_graph_runs_parallel_agents_under_the_root(px, scripted_model):
    from strands import Agent
    from strands.multiagent import GraphBuilder

    _, exporter, _ = px
    builder = GraphBuilder()
    for name in ("stats", "lore"):
        builder.add_node(
            Agent(
                name=name,
                model=scripted_model(name, lambda messages, name=name: _reply(f"listo {name}")),
                system_prompt=name,
                hooks=[telemetry.SubAgentTrace()],
                callback_handler=None,
            ),
            name,
        )
        builder.set_entry_point(name)
    graph = builder.set_execution_timeout(30).build()

    async def run():
        with telemetry.agent_span("conv-12345678") as root:
            await graph.invoke_async("mew")
            assert telemetry._current_agent.get() is root

    asyncio.run(run())
    tree = _tree(exporter)
    assert sorted(tree["dexter"]) == ["lore", "stats"]
    assert (tree["stats"], tree["lore"]) == (["chat"], ["chat"])
    spans = exporter.get_finished_spans()
    assert len({s.context.trace_id for s in spans}) == 1
    # The two nodes really overlapped: each ran in its own task, with its own copy of the context.
    stats, lore = _by_name(exporter)["stats"], _by_name(exporter)["lore"]
    assert stats.start_time < lore.end_time and lore.start_time < stats.end_time


def test_strands_sub_agent_outside_a_request_has_no_span(px, scripted_model):
    from strands import Agent

    _, exporter, _ = px
    researcher = Agent(
        name="researcher",
        model=scripted_model("researcher", lambda messages: _reply("Mew.")),
        system_prompt="researcher",
        hooks=[telemetry.SubAgentTrace()],
        callback_handler=None,
    )
    researcher("mew")
    assert [s.attributes["openinference.span.kind"] for s in exporter.get_finished_spans()] == ["LLM"]
