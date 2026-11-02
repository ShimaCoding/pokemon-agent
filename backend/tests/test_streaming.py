"""End-to-end streaming guarantees for POST /api/agent/run (the agent is faked).

These drive the ASGI app directly, through the full middleware stack, and watch
each ``send``: TestClient and httpx's ASGITransport collect the whole body first,
so they cannot tell a streamed response from a buffered one.
"""

import asyncio
import json
from types import SimpleNamespace

from backend import agent as agent_mod
from backend import main, telemetry

_STEP_S = 0.3  # simulated model/tool latency
_STEP_MS = int(_STEP_S * 1000)


class _FakeAgent:
    """Stands in for the harness agent; ``script(agent)`` is its ``stream_async``."""

    tool_names = ["get_pokedex_entry"]
    messages: list = []

    def __init__(self, script, trace, hooks):
        self._script = script
        self.trace = trace
        self.tool_trace = hooks[0]

    def stream_async(self, prompt):
        return self._script(self)

    def cleanup(self):
        pass


def _result_event() -> dict:
    usage = {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3}
    return {"result": SimpleNamespace(metrics=SimpleNamespace(accumulated_usage=usage))}


def _run(monkeypatch, script, on_send=None):
    """POST /api/agent/run against ``main.app``; return (sent ASGI messages, decoded SSE events).

    ``on_send(msg)`` runs after each message has been handed to the server.
    """
    monkeypatch.setattr(main, "log_query", lambda **kw: None)
    monkeypatch.setattr(telemetry, "_enabled", False)
    monkeypatch.setattr(
        agent_mod, "create_agent", lambda *a, trace, hooks, **k: _FakeAgent(script, trace, hooks)
    )
    main.limiter.reset()

    async def drive():
        body = json.dumps({"query": "háblame de bulbasaur", "conversation_id": "test-conv-0001"}).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/agent/run",
            "raw_path": b"/api/agent/run",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        sent: list[dict] = []
        finished = asyncio.Event()
        delivered = False

        async def receive():
            # The request body once, then block until the response is complete:
            # BaseHTTPMiddleware rejects a second http.request message.
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            sent.append(msg)
            if msg["type"] == "http.response.body" and not msg.get("more_body", False):
                finished.set()
            if on_send is not None:
                on_send(msg)

        await main.app(scope, receive, send)
        return sent

    sent = asyncio.run(drive())
    frames = [m for m in sent if m["type"] == "http.response.body" and m.get("body")]
    events = [json.loads(m["body"].decode()[len("data:") :]) for m in frames]
    return sent, events


def test_text_deltas_reach_the_socket_before_the_agent_continues(monkeypatch):
    first_sent = asyncio.Event()

    async def script(agent):
        yield {"data": "Bulbasaur"}
        # If any layer (middleware, StreamingResponse) buffered the first frame,
        # it never reaches `send`, this times out and the run ends in `error`.
        await asyncio.wait_for(first_sent.wait(), timeout=2)
        yield {"data": ", Pokémon Semilla."}
        yield _result_event()

    def on_send(msg):
        if msg["type"] == "http.response.body" and b'"type": "text"' in msg.get("body", b""):
            first_sent.set()

    sent, events = _run(monkeypatch, script, on_send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["x-accel-buffering"] == "no"
    assert headers["cache-control"] == "no-cache"
    assert "content-encoding" not in headers

    assert [e["type"] for e in events] == ["start", "agent_init", "text", "text", "done"]
    # One SSE frame per ASGI body message: nothing coalesced along the way.
    bodies = [m["body"] for m in sent if m["type"] == "http.response.body" and m.get("body")]
    assert all(b.endswith(b"\n\n") and b.count(b"data:") == 1 for b in bodies)


def test_ttft_is_first_text_not_first_event(monkeypatch):
    async def script(agent):
        agent.trace({"type": "llm_call", "model": "groq/openai/gpt-oss-20b", "messages": []})
        yield {"event": {"messageStart": {"role": "assistant"}}}  # a non-text stream event
        await asyncio.sleep(_STEP_S)
        yield {"data": "BULBASAUR."}
        yield _result_event()

    _, events = _run(monkeypatch, script)
    types = [e["type"] for e in events]
    done = events[-1]

    # Trace frames before the text must not count as "first token".
    assert types.index("llm_call") < types.index("text")
    assert done["type"] == "done"
    assert done["ttft_ms"] >= _STEP_MS

