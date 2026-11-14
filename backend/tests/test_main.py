"""Tests for the HTTP surface in backend/main.py (MCP calls are faked)."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import main, mcp_gateway

STATS_URI = "ui://pokedex/stats.html"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(main, "init_db", lambda: None)
    main.limiter.reset()
    with TestClient(main.app) as c:
        yield c


def test_health_reports_version(client, monkeypatch):
    monkeypatch.setenv("DD_VERSION", "abc1234")
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == "abc1234"


# --- /api/agent/run validation ----------------------------------------------


@pytest.mark.parametrize("conversation_id", ["../../etc", "UPPER-CASE-ID", "short", ""])
def test_run_rejects_bad_conversation_ids(client, conversation_id):
    res = client.post("/api/agent/run", json={"query": "hola", "conversation_id": conversation_id})
    assert res.status_code == 422


def test_run_requires_conversation_id(client):
    res = client.post("/api/agent/run", json={"query": "hola"})
    assert res.status_code == 422


# --- Telemetry on setup failures ----------------------------------------------------


@pytest.fixture
def failed_requests(monkeypatch):
    from backend import telemetry

    recorded = []
    monkeypatch.setattr(main, "log_query", lambda **kw: None)
    monkeypatch.setattr(
        telemetry,
        "record_failed_request",
        lambda session_id, exc, *, input_data=None: recorded.append((session_id, str(exc), input_data)),
    )
    return recorded


def _run_events(client) -> list[dict]:
    import json

    res = client.post("/api/agent/run", json={"query": "hola", "conversation_id": "conv-12345678"})
    assert res.status_code == 200
    return [json.loads(line[len("data: ") :]) for line in res.text.splitlines() if line.startswith("data: ")]


def test_setup_failure_records_error_span(client, monkeypatch, failed_requests):
    """MCP down (create_agent raises before the agent span opens) still reaches the error monitor."""
    from backend import agent

    def create_agent(*args, **kwargs):
        raise RuntimeError("mcp down")

    monkeypatch.setattr(agent, "create_agent", create_agent)
    assert [e["type"] for e in _run_events(client)] == ["start", "error", "done"]
    assert failed_requests == [("conv-12345678", "mcp down", "hola")]


def test_run_failure_is_left_to_the_agent_span(client, monkeypatch, failed_requests):
    """A failure inside the agent span is already marked there; no second error span."""
    from backend import agent

    async def stream_async(prompt):
        raise RuntimeError("groq down")
        yield  # pragma: no cover

    fake = SimpleNamespace(tool_names=[], messages=[], stream_async=stream_async, cleanup=lambda: None)
    monkeypatch.setattr(agent, "create_agent", lambda *args, **kwargs: fake)
    assert [e["type"] for e in _run_events(client)] == ["start", "agent_init", "error", "done"]
    assert failed_requests == []


# --- /api/ui-resource ---------------------------------------------------------


def test_ui_resource_rejects_non_ui_scheme(client):
    assert client.get("/api/ui-resource", params={"uri": "https://evil.example"}).status_code == 400


def test_ui_resource_unknown_uri_is_404(client, monkeypatch):
    monkeypatch.setattr(mcp_gateway, "read_ui_resource", lambda uri: None)
    assert client.get("/api/ui-resource", params={"uri": "ui://nope/x.html"}).status_code == 404


def test_ui_resource_returns_html_and_csp(client, monkeypatch):
    payload = {"uri": STATS_URI, "mimeType": "text/html;profile=mcp-app", "html": "<!doctype html>", "csp": {}}
    monkeypatch.setattr(mcp_gateway, "read_ui_resource", lambda uri: payload if uri == STATS_URI else None)
    res = client.get("/api/ui-resource", params={"uri": STATS_URI})
    assert res.status_code == 200
    assert res.json() == payload


# --- /api/mcp/call-tool -------------------------------------------------------


def test_call_tool_forbids_tools_without_ui(client, monkeypatch):
    monkeypatch.setattr(mcp_gateway, "call_ui_tool", lambda name, args: None)
    res = client.post("/api/mcp/call-tool", json={"name": "search_pokemon", "arguments": {}})
    assert res.status_code == 403


def test_call_tool_forwards_ui_tools(client, monkeypatch):
    result = {"content": [{"type": "text", "text": "ok"}], "isError": False, "structuredContent": {"pokemon": []}}
    monkeypatch.setattr(mcp_gateway, "call_ui_tool", lambda name, args: result)
    res = client.post("/api/mcp/call-tool", json={"name": "compare_pokemon", "arguments": {"pokemon_names": ["a", "b"]}})
    assert res.status_code == 200
    assert res.json() == result


# --- mcp_gateway allowlists -------------------------------------------------------


def test_gateway_only_calls_ui_bound_tools(monkeypatch):
    monkeypatch.setattr(mcp_gateway, "ui_tools", lambda: {"compare_pokemon": STATS_URI})
    monkeypatch.setattr(mcp_gateway, "_client", lambda: pytest.fail("must not connect"))
    assert mcp_gateway.call_ui_tool("search_pokemon", {}) is None
    assert mcp_gateway.read_ui_resource("ui://other/x.html") is None


def test_to_call_tool_result():
    strands_result = {
        "status": "success",
        "toolUseId": "t1",
        "content": [{"text": "hola"}],
        "structuredContent": {"id": 25},
    }
    assert mcp_gateway.to_call_tool_result(strands_result) == {
        "content": [{"type": "text", "text": "hola"}],
        "isError": False,
        "structuredContent": {"id": 25},
    }


# --- Tool hook → SSE events ---------------------------------------------------------


def _tool_events(name: str, meta: dict | None, status: str = "success"):
    events: list[dict] = []
    trace = main._ToolTrace(events.append)
    tool_use = {"toolUseId": "tu-1", "name": name, "input": {"pokemon_names": ["pikachu", "snorlax"]}}
    trace._on_call(SimpleNamespace(tool_use=tool_use))
    selected = SimpleNamespace(mcp_tool=SimpleNamespace(meta=meta)) if meta is not None else SimpleNamespace()
    result = {"status": status, "content": [{"text": "tabla"}], "structuredContent": {"pokemon": []}}
    trace._on_result(SimpleNamespace(tool_use=tool_use, result=result, selected_tool=selected))
    return events


def test_ui_bound_tool_emits_ui_resource():
    events = _tool_events("compare_pokemon", {"ui": {"resourceUri": STATS_URI}})
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "ui_resource"]
    ui = events[-1]
    assert ui["tool_id"] == "tu-1"
    assert ui["index"] == events[0]["index"] == 0
    assert ui["resource_uri"] == STATS_URI
    assert ui["tool_input"] == {"pokemon_names": ["pikachu", "snorlax"]}
    assert ui["tool_result"]["structuredContent"] == {"pokemon": []}


def test_plain_tool_emits_no_ui_resource():
    events = _tool_events("search_pokemon", None)
    assert [e["type"] for e in events] == ["tool_call", "tool_result"]


def test_failed_ui_tool_emits_no_widget():
    events = _tool_events("compare_pokemon", {"ui": {"resourceUri": STATS_URI}}, status="error")
    assert "ui_resource" not in [e["type"] for e in events]


def test_unknown_tool_result_passes_through():
    # An old conversation may still call the removed `skills` tool (#16): its
    # error result is shown as is, with no widget.
    events = _tool_events("skills", None, status="error")
    assert [e["type"] for e in events] == ["tool_call", "tool_result"]
    assert events[1]["result"] == "tabla"


# --- /api/agent/run turn counter ----------------------------------------------------


class _StubAgent:
    """Just enough of a strands Agent for _sse_generator, without MCP or a model."""

    def __init__(self, messages):
        from strands.agent.state import AgentState

        self.tool_names = ["get_pokedex_entry"]
        self.messages = messages
        self.state = AgentState()

    async def stream_async(self, prompt):
        self.messages.append({"role": "user", "content": [{"text": prompt}]})
        yield {"data": "Escaneo completado."}
        yield {"result": SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={"inputTokens": 10}))}

    def cleanup(self):
        pass


# --- Agent run: `done` ids and post-done rule evals ---------------------------------------

POKEDEX_JSON = (
    '{"id": 1, "name": "bulbasaur", "genus": "Pokémon Semilla", '
    '"base_stats": {"hp": 45, "attack": 49, "defense": 49, "special-attack": 65, "special-defense": 65, "speed": 45}}'
)
IDS = {"trace_id": "ab" * 16, "span_id": "1234567890"}


class _FakeAgent:
    """Stands in for the harness agent: one get_pokedex_entry call, then a narrated reply."""

    tool_names = ["get_pokedex_entry"]

    def __init__(self, hooks):
        self._trace = hooks[0]
        self.messages = [{"role": "user", "content": [{"text": "háblame de bulbasaur"}]}]

    async def stream_async(self, prompt):
        tool_use = {"toolUseId": "tu-1", "name": "get_pokedex_entry", "input": {"pokemon": "bulbasaur"}}
        self._trace._on_call(SimpleNamespace(tool_use=tool_use))
        result = {"status": "success", "content": [{"text": POKEDEX_JSON}]}
        self._trace._on_result(SimpleNamespace(tool_use=tool_use, result=result, selected_tool=SimpleNamespace()))
        self.messages += [
            {"role": "assistant", "content": [{"toolUse": tool_use}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "tu-1", **result}}]},
        ]
        yield {"data": "BULBASAUR. El Pokémon Semilla. Velocidad 45; tu intelecto, "}
        yield {"data": "por desgracia, no figura en mis registros."}
        yield {"result": SimpleNamespace(metrics=SimpleNamespace(accumulated_usage={}))}

    def cleanup(self):
        pass


def _sse_events(res) -> list[dict]:
    import json

    return [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]


def test_run_reports_conversation_turn(client, monkeypatch):
    from backend import agent as agent_module
    from backend import telemetry

    history = [
        {"role": "user", "content": [{"text": "háblame de bulbasaur"}]},
        {"role": "assistant", "content": [{"text": "BULBASAUR. El Pokémon Semilla."}]},
    ]
    stub = _StubAgent(history)
    annotations: list[dict] = []
    monkeypatch.setattr(agent_module, "create_agent", lambda *args, **kwargs: stub)
    monkeypatch.setattr(main, "log_query", lambda **kwargs: None)
    monkeypatch.setattr(telemetry, "annotate", lambda span, **fields: annotations.append(fields))

    body = {"query": "¿y la de charmander?", "conversation_id": "turn-test-1234"}
    events = _sse_events(client.post("/api/agent/run", json=body))
    init = next(e for e in events if e["type"] == "agent_init")
    # Restored history without a counter (pre-#18 snapshot): 1 past turn, so this is turn 2.
    assert init["turn"] == 2
    assert init["history_messages"] == 2
    assert annotations[-1]["metadata"]["turn"] == 2
    assert annotations[-1]["metadata"]["history_messages"] == 3

    events = _sse_events(client.post("/api/agent/run", json=body))
    assert next(e for e in events if e["type"] == "agent_init")["turn"] == 3


def test_tool_result_reports_cache_status_and_duration():
    events: list[dict] = []
    trace = main._ToolTrace(events.append)
    tool_use = {"toolUseId": "tu-1", "name": "get_pokedex_entry", "input": {"pokemon": "pikachu"}}
    trace._on_call(SimpleNamespace(tool_use=tool_use))
    result = {"status": "success", "content": [{"text": "{}"}]}
    state = {"mcpokedex:tool_cache": {"tu-1": "hit"}}
    trace._on_result(
        SimpleNamespace(tool_use=tool_use, result=result, selected_tool=None, invocation_state=state, duration=0.0004)
    )
    assert events[1]["cached"] is True
    assert events[1]["duration_ms"] == 0.4


def test_tool_result_without_cache_status():
    events = _tool_events("search_pokemon", None)
    assert events[1]["cached"] is False
    assert events[1]["duration_ms"] is None


@pytest.fixture
def fake_run(monkeypatch):
    """Patch the agent factory and telemetry so `_sse_generator` runs offline; returns captured evals."""
    from contextlib import nullcontext

    from backend import agent as agent_module, telemetry
    from backend.feedback import FeedbackRegistry

    monkeypatch.setattr(agent_module, "create_agent", lambda conversation_id, **kw: _FakeAgent(kw["hooks"]))
    monkeypatch.setattr(main, "log_query", lambda **kw: None)
    monkeypatch.setattr(main, "feedback_registry", FeedbackRegistry())
    monkeypatch.setattr(telemetry, "agent_span", lambda session_id: nullcontext(object()))
    monkeypatch.setattr(telemetry, "annotate", lambda span, **kw: None)
    monkeypatch.setattr(telemetry, "export_ids", lambda span: dict(IDS))
    submitted = []
    monkeypatch.setattr(telemetry, "submit_evaluations", lambda ids, results: submitted.append((ids, results)))
    return submitted


def _run_eval_events(stop_after_done: bool = False) -> list[dict]:
    import asyncio
    import json

    async def collect():
        events = []
        gen = main._sse_generator("háblame de bulbasaur", "conv-12345678")
        async for frame in gen:
            events.append(json.loads(frame[len("data: ") :]))
            if stop_after_done and events[-1]["type"] == "done":
                await gen.aclose()  # what a client disconnect does to the generator
                break
        return events

    return asyncio.run(collect())


def test_done_carries_ids_and_rule_evals_are_submitted(fake_run):
    events = _run_eval_events()
    done = events[-1]
    assert done["type"] == "done"
    assert (done["trace_id"], done["span_id"]) == (IDS["trace_id"], IDS["span_id"])

    [(ids, results)] = fake_run
    assert ids == IDS
    assert main.feedback_registry.claim(IDS["span_id"], IDS["trace_id"]) == ("ok", "conv-12345678")
    by_label = {r.label: r for r in results}
    assert set(by_label) == {"pokemon_tool_used", "dexter_persona", "grounded_facts"}
    assert all(r.passed for r in results), [(r.label, r.reasoning) for r in results]


def test_no_ids_and_no_evals_when_telemetry_disabled(fake_run, monkeypatch):
    from backend import telemetry

    monkeypatch.setattr(telemetry, "export_ids", lambda span: None)
    done = _run_eval_events()[-1]
    assert "trace_id" not in done and "span_id" not in done
    assert fake_run == []
    assert len(main.feedback_registry) == 0


def test_evals_skipped_when_client_disconnects_at_done(fake_run):
    assert _run_eval_events(stop_after_done=True)[-1]["type"] == "done"
    assert fake_run == []


def test_eval_errors_never_break_the_stream(fake_run, monkeypatch):
    from backend.evals import checks

    monkeypatch.setattr(checks, "evaluate_turn", lambda turn: 1 / 0)
    events = _run_eval_events()
    assert events[-1]["type"] == "done"
    assert "error" not in [e["type"] for e in events]


# --- RATE_LIMIT_DISABLED (local evals only) ---------------------------------------


@pytest.fixture
def quick_run(client, monkeypatch):
    """/api/agent/run without the agent: each call is one hit on the rate limit."""

    async def gen(query, conversation_id, *, authenticated):
        yield "data: {}\n\n"

    monkeypatch.setattr(main, "_sse_generator", gen)
    monkeypatch.delenv("API_KEY", raising=False)

    def post() -> int:
        return client.post("/api/agent/run", json={"query": "hola", "conversation_id": "conv-12345678"}).status_code

    return post


@pytest.mark.parametrize(("flag", "env", "disabled"), [
    ("1", "", True),
    ("true", "local", True),
    ("1", "dev", True),
    ("1", "prod", False),
    ("1", "PROD", False),
    ("1", "preview", False),
    ("", "", False),
    ("0", "local", False),
])
def test_rate_limit_disabled_only_outside_prod(monkeypatch, flag, env, disabled):
    monkeypatch.setenv("RATE_LIMIT_DISABLED", flag)
    monkeypatch.setenv("DD_ENV", env)
    assert main.rate_limit_disabled() is disabled


def test_agent_run_keeps_limit_by_default(quick_run, monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
    assert [quick_run() for _ in range(4)] == [200, 200, 200, 429]


def test_agent_run_limit_lifted_in_dev(quick_run, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    monkeypatch.setenv("DD_ENV", "local")
    assert {quick_run() for _ in range(6)} == {200}


def test_rate_limit_flag_has_no_effect_in_prod(quick_run, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    monkeypatch.setenv("DD_ENV", "prod")
    assert [quick_run() for _ in range(4)] == [200, 200, 200, 429]
