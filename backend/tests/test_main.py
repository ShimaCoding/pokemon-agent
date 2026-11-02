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


# --- /api/agent/run validation ----------------------------------------------


@pytest.mark.parametrize("conversation_id", ["../../etc", "UPPER-CASE-ID", "short", ""])
def test_run_rejects_bad_conversation_ids(client, conversation_id):
    res = client.post("/api/agent/run", json={"query": "hola", "conversation_id": conversation_id})
    assert res.status_code == 422


def test_run_requires_conversation_id(client):
    res = client.post("/api/agent/run", json={"query": "hola"})
    assert res.status_code == 422


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
