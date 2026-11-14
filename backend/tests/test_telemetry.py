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
    assert telemetry_phoenix._current_agent.get() is None


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
