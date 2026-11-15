"""backend/traces.py: runs read back from Phoenix for /admin/traces.

The Phoenix answers used here have the shape of Phoenix 21's REST API (checked against a
real server). The span attributes are not written by hand: the tests emit spans through
backend.telemetry and reshape what it exports, so a change in what telemetry writes that
the reader does not follow fails here.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend import main, telemetry, telemetry_phoenix, traces

MODEL = "groq/openai/gpt-oss-120b"
PROJECT = "mcpokedex-test"


@pytest.fixture
def exporter(monkeypatch):
    """Telemetry writing to memory, like the Phoenix backend does in production."""
    exporter = InMemorySpanExporter()
    backend = telemetry_phoenix.PhoenixBackend(
        endpoint="http://phoenix:6006",
        api_key="secret",
        project=PROJECT,
        env="test",
        version="abc1234",
        processor=SimpleSpanProcessor(exporter),
    )
    monkeypatch.setattr(telemetry, "_dd_enabled", False)
    monkeypatch.setattr(telemetry, "_phoenix", backend)
    yield exporter
    backend.shutdown()
    telemetry._current_agent.set(None)


def _iso(nanoseconds: int) -> str:
    return datetime.fromtimestamp(nanoseconds / 1e9, tz=timezone.utc).isoformat()


def _as_phoenix(span) -> dict:
    """An exported OTel span → the span Phoenix's GET /spans returns for it."""
    attributes = dict(span.attributes)
    kind = attributes.pop("openinference.span.kind")
    # Phoenix parses the metadata JSON and flattens it like every other attribute.
    for key, value in json.loads(attributes.pop("metadata", "{}")).items():
        attributes[f"metadata.{key}"] = value
    return {
        "id": "U3Bhbjox",
        "name": span.name,
        "context": {"trace_id": format(span.context.trace_id, "032x"), "span_id": format(span.context.span_id, "016x")},
        "span_kind": kind,
        "parent_id": format(span.parent.span_id, "016x") if span.parent else None,
        "start_time": _iso(span.start_time),
        "end_time": _iso(span.end_time),
        "status_code": span.status.status_code.name,
        "status_message": span.status.description or "",
        "attributes": attributes,
        "events": [{"name": e.name, "attributes": dict(e.attributes)} for e in span.events],
    }


def _compact(span: dict) -> dict:
    """The short span form inside GET /traces?include_spans=true."""
    return {
        "id": span["id"],
        "span_id": span["context"]["span_id"],
        "parent_id": span["parent_id"],
        "name": span["name"],
        "span_kind": span["span_kind"],
        "status_code": span["status_code"],
        "start_time": span["start_time"],
        "end_time": span["end_time"],
    }


class FakePhoenix:
    """Phoenix's REST API over the spans telemetry exported; records every request."""

    def __init__(self, exporter):
        self.exporter = exporter
        self.requests: list[httpx.Request] = []

    def spans(self) -> list[dict]:
        return [_as_phoenix(s) for s in self.exporter.get_finished_spans()]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix(f"/v1/projects/{PROJECT}")
        params = request.url.params
        spans = sorted(self.spans(), key=lambda s: s["start_time"])
        if path == "":
            return httpx.Response(200, json={"data": {"name": PROJECT, "id": "UHJvamVjdDoy"}})
        if path == "/traces":
            by_trace: dict[str, list[dict]] = {}
            for span in spans:
                by_trace.setdefault(span["context"]["trace_id"], []).append(span)
            data = [
                {
                    "trace_id": trace_id,
                    "start_time": min(s["start_time"] for s in group),
                    "end_time": max(s["end_time"] for s in group),
                    "token_count_total": sum(s["attributes"].get("llm.token_count.total", 0) for s in group),
                    "spans": [_compact(s) for s in group],
                }
                for trace_id, group in by_trace.items()
            ]
            data.sort(key=lambda t: t["start_time"], reverse=params["order"] == "desc")
            return httpx.Response(200, json={"data": data[: int(params["limit"])], "next_cursor": None})
        if path == "/spans":
            wanted = set(params.get_list("trace_id"))
            data = [s for s in spans if s["context"]["trace_id"] in wanted]
            if params.get("parent_id") == "null":
                data = [s for s in data if s["parent_id"] is None]
            return httpx.Response(200, json={"data": data, "next_cursor": None})
        return httpx.Response(404)


@pytest.fixture
def phoenix(exporter):
    return FakePhoenix(exporter)


def _reader(phoenix, public_url: str = "https://phoenix.test/") -> traces.PhoenixReader:
    return traces.PhoenixReader(
        base_url="http://phoenix:6006",
        headers={"authorization": "Bearer secret"},
        project=PROJECT,
        public_url=public_url,
        http=httpx.Client(transport=httpx.MockTransport(phoenix)),
    )


def _llm(prompt: str, reply: str, tool_calls: list[str] | None = None, tokens=(100, 20, 64)) -> None:
    with telemetry.llm_span(MODEL) as span:
        message = {"role": "assistant", "content": reply}
        if tool_calls:
            message["tool_calls"] = [{"name": name, "arguments": {}, "tool_id": name} for name in tool_calls]
        telemetry.annotate(
            span,
            input_data=[{"role": "system", "content": "Eres Dexter"}, {"role": "user", "content": prompt}],
            output_data=[message],
            metrics={
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
                "total_tokens": tokens[0] + tokens[1],
                "cache_read_input_tokens": tokens[2],
            },
            metadata={"call_role": "routing"},
        )


def _tool(name: str, args: dict, output: str, error: bool = False) -> None:
    span = telemetry.start_tool_span(name)
    telemetry.finish_tool_span(span, input_data=args, output_data=output, error=error, tags={"cache": "hit"})


def _run(query: str = "¿cuánto pesa mew?") -> str:
    """One request: dexter calls a model, hands off to a sub-agent that uses a tool, and answers."""
    with telemetry.agent_span("conv-12345678") as root:
        telemetry.annotate(root, input_data=query)
        _llm(query, "[toolUse researcher]", tool_calls=["researcher"])
        with telemetry.sub_agent_span("researcher") as sub:
            telemetry.annotate(sub, input_data="peso de mew")
            _llm("peso de mew", "Voy a buscarlo.\n[toolUse get_pokedex_entry]", tokens=(50, 5, 0))
            _tool("get_pokedex_entry", {"pokemon": "mew"}, '{"id": 151, "weight": 40}')
            telemetry.annotate(sub, output_data="Mew pesa 4 kg.")
        _llm(query, "MEW. 4 kg.")
        telemetry.annotate(root, output_data="MEW. 4 kg.")
        return telemetry.export_ids(root)["trace_id"]


# --- One run with every step ----------------------------------------------------------------


def test_trace_keeps_the_tree_and_the_order(phoenix):
    trace_id = _run()
    trace = _reader(phoenix).get_trace(trace_id)

    spans = trace["spans"]
    assert [(s["kind"], s["name"]) for s in spans] == [
        ("agent", "dexter"),
        ("llm", "openai/gpt-oss-120b"),
        ("agent", "researcher"),
        ("llm", "openai/gpt-oss-120b"),
        ("tool", "get_pokedex_entry"),
        ("llm", "openai/gpt-oss-120b"),
    ]
    root, first, sub, inner, tool, last = spans
    assert root["parentId"] is None
    assert {first["parentId"], sub["parentId"], last["parentId"]} == {root["id"]}
    assert {inner["parentId"], tool["parentId"]} == {sub["id"]}
    # Offsets from the start of the run, in the order things happened.
    assert root["startMs"] == 0
    assert [s["startMs"] for s in spans] == sorted(s["startMs"] for s in spans)
    assert all(s["durationMs"] >= 0 and not s["error"] for s in spans)

    assert trace["traceId"] == trace_id
    assert (trace["query"], trace["spanCount"], trace["subAgents"], trace["error"]) == ("¿cuánto pesa mew?", 6, 1, False)


def test_model_call_shows_messages_reply_and_tokens(phoenix):
    spans = _reader(phoenix).get_trace(_run())["spans"]
    first, inner, last = (s for s in spans if s["kind"] == "llm")

    assert first["input"] == [
        {"role": "system", "content": "Eres Dexter"},
        {"role": "user", "content": "¿cuánto pesa mew?"},
    ]
    assert first["tokens"] == {"prompt": 100, "completion": 20, "cached": 64}
    assert first["metadata"]["call_role"] == "routing"
    # The reply text repeats the tool call as a marker: the names stay, the marker goes.
    assert first["toolCalls"] == ["researcher"] and "output" not in first
    assert inner["toolCalls"] == ["get_pokedex_entry"] and inner["output"] == "Voy a buscarlo."
    assert "toolCalls" not in last and last["output"] == "MEW. 4 kg."


def test_tool_call_shows_arguments_result_and_cache(phoenix):
    spans = _reader(phoenix).get_trace(_run())["spans"]
    [tool] = (s for s in spans if s["kind"] == "tool")
    assert tool["input"] == {"pokemon": "mew"}
    assert tool["output"] == '{"id": 151, "weight": 40}'
    assert tool["metadata"]["cache"] == "hit"
    assert "tokens" not in tool


def test_agents_add_up_the_tokens_of_everything_under_them(phoenix):
    trace = _reader(phoenix).get_trace(_run())
    root, sub = (s for s in trace["spans"] if s["kind"] == "agent")
    assert sub["tokens"] == {"prompt": 50, "completion": 5}
    assert root["tokens"] == {"prompt": 250, "completion": 45}  # its two calls and the sub-agent's
    assert trace["tokens"] == 295
    assert (sub["input"], sub["output"]) == ("peso de mew", "Mew pesa 4 kg.")
    assert (root["metadata"]["agent_level"], sub["metadata"]["agent_level"]) == ("root", "sub")


def test_errors_carry_their_message(phoenix):
    with pytest.raises(RuntimeError):
        with telemetry.agent_span("conv-12345678") as root:
            trace_id = telemetry.export_ids(root)["trace_id"]
            _tool("get_pokedex_entry", {"pokemon": "mew"}, "boom", error=True)
            with telemetry.llm_span(MODEL):
                raise RuntimeError("groq rate limited")

    trace = _reader(phoenix).get_trace(trace_id)
    root, tool, llm = trace["spans"]
    assert trace["error"] and root["error"] and tool["error"] and llm["error"]
    assert llm["errorMessage"] == "RuntimeError: groq rate limited"
    assert root["errorMessage"] == "RuntimeError: groq rate limited"
    assert "errorMessage" not in tool  # failed without an exception: nothing to quote


def test_long_texts_are_cut(phoenix):
    with telemetry.agent_span("conv-12345678") as root:
        trace_id = telemetry.export_ids(root)["trace_id"]
        telemetry.annotate(root, input_data="x" * 500)
        _llm("y" * (traces.TEXT_CHARS + 500), "ok")
    trace = _reader(phoenix).get_trace(trace_id)
    assert len(trace["query"]) == traces.QUERY_CHARS + 1 and trace["query"].endswith("…")
    user = trace["spans"][1]["input"][1]["content"]
    assert len(user) == traces.TEXT_CHARS + 1 and user.endswith("…")


def test_trace_links_to_phoenix_by_project_id(phoenix):
    trace_id = _run()
    reader = _reader(phoenix)
    assert reader.get_trace(trace_id)["phoenixUrl"] == f"https://phoenix.test/projects/UHJvamVjdDoy/traces/{trace_id}"
    reader.get_trace(trace_id)
    # The project id is asked for once.
    assert [r.url.path for r in phoenix.requests].count(f"/v1/projects/{PROJECT}") == 1

    assert _reader(phoenix, public_url="").get_trace(trace_id)["phoenixUrl"] is None


def test_the_link_telemetry_writes_opens_that_run(phoenix, monkeypatch):
    """Phoenix → admin: metadata.admin_url on the root span must be a URL this app serves and the page reads."""
    import re
    from pathlib import Path
    from urllib.parse import parse_qs, urlsplit

    root = Path(__file__).resolve().parents[2]
    # ADMIN_TRACES_URL as documented in .env.example.
    example = re.search(r"^ADMIN_TRACES_URL=.*?(https://\S+?);", (root / ".env.example").read_text(), re.M).group(1)
    monkeypatch.setattr(telemetry._phoenix, "admin_traces_url", example)
    trace_id = _run()

    trace = _reader(phoenix).get_trace(trace_id)
    url = urlsplit(trace["spans"][0]["metadata"]["admin_url"])
    assert url.path in {route.path for route in main.app.routes}  # the page main.py serves
    [(param, [value])] = parse_qs(url.query).items()
    assert value == trace_id and traces.TRACE_ID_RE.fullmatch(value)  # an id the API accepts
    # …and the query parameter the page opens a run from.
    page = (root / "frontend" / "src" / "traces" / "TracesApp.tsx").read_text()
    assert f"params.get('{param}')" in page


def test_unknown_trace_is_none(phoenix):
    _run()
    assert _reader(phoenix).get_trace("0" * 32) is None


# --- Recent queries ---------------------------------------------------------------------------


def test_list_is_newest_first_with_counts(phoenix):
    first = _run("¿cuánto pesa mew?")
    with pytest.raises(RuntimeError):
        with telemetry.agent_span("conv-87654321") as root:
            second = telemetry.export_ids(root)["trace_id"]
            telemetry.annotate(root, input_data="háblame de pikachu")
            with telemetry.llm_span(MODEL):
                raise RuntimeError("groq down")

    listed = _reader(phoenix).list_traces()
    assert [t["traceId"] for t in listed] == [second, first]
    newest, oldest = listed
    assert (newest["query"], newest["error"], newest["spanCount"], newest["subAgents"]) == ("háblame de pikachu", True, 2, 0)
    assert (oldest["query"], oldest["error"], oldest["spanCount"], oldest["subAgents"]) == ("¿cuánto pesa mew?", False, 6, 1)
    assert oldest["tokens"] == 295 and oldest["durationMs"] >= 0
    assert datetime.fromisoformat(oldest["startedAt"]) <= datetime.fromisoformat(newest["startedAt"])


def test_list_asks_phoenix_for_compact_traces_and_their_roots(phoenix):
    _run()
    _reader(phoenix).list_traces(limit=5)

    traces_request, roots_request = phoenix.requests
    assert traces_request.url.path == f"/v1/projects/{PROJECT}/traces"
    assert dict(traces_request.url.params) == {"limit": "5", "sort": "start_time", "order": "desc", "include_spans": "true"}
    assert roots_request.url.path == f"/v1/projects/{PROJECT}/spans"
    assert roots_request.url.params["parent_id"] == "null"
    assert all(r.headers["authorization"] == "Bearer secret" for r in phoenix.requests)


def test_empty_project_lists_nothing(phoenix):
    assert _reader(phoenix).list_traces() == []
    assert len(phoenix.requests) == 1  # no second call for roots


def test_reader_follows_telemetry(exporter, monkeypatch):
    monkeypatch.setenv("PHOENIX_PUBLIC_URL", "https://phoenix.test")
    reader = traces.reader()
    assert reader is traces.reader()
    assert reader._project_url == f"http://phoenix:6006/v1/projects/{PROJECT}"
    assert reader._headers == {"authorization": "Bearer secret"}

    monkeypatch.setattr(telemetry, "_phoenix", None)
    assert traces.reader() is None


# --- /admin/traces and its API -----------------------------------------------------------------

KEY = {"X-API-Key": "admin-key"}
TRACE_ID = "ab" * 16


class StubReader:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.limits: list[int] = []

    def list_traces(self, limit: int):
        if self.error:
            raise self.error
        self.limits.append(limit)
        return [{"traceId": TRACE_ID}]

    def get_trace(self, trace_id: str):
        if self.error:
            raise self.error
        return {"traceId": trace_id, "spans": []} if trace_id == TRACE_ID else None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("API_KEY", "admin-key")
    monkeypatch.setattr(main, "init_db", lambda: None)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def stub(monkeypatch):
    reader = StubReader()
    monkeypatch.setattr(traces, "reader", lambda: reader)
    return reader


def test_page_is_served(client):
    res = client.get("/admin/traces")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    assert 'id="root"' in res.text


@pytest.mark.parametrize("path", ["/admin/api/traces", f"/admin/api/traces/{TRACE_ID}"])
def test_api_needs_the_admin_key(client, stub, path):
    # Runs hold what visitors asked: same key as the rest of the admin panel.
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get(path, headers=KEY).status_code == 200


def test_api_lists_and_reads(client, stub):
    assert client.get("/admin/api/traces", headers=KEY).json() == {"traces": [{"traceId": TRACE_ID}]}
    client.get("/admin/api/traces?limit=7", headers=KEY)
    assert stub.limits == [30, 7]
    assert client.get("/admin/api/traces?limit=5000", headers=KEY).status_code == 422
    assert client.get(f"/admin/api/traces/{TRACE_ID}", headers=KEY).json() == {"traceId": TRACE_ID, "spans": []}


@pytest.mark.parametrize("trace_id", ["cd" * 16, "not-a-trace-id", "AB" * 16, "ab" * 16 + "0"])
def test_api_unknown_or_malformed_trace_is_404(client, stub, trace_id):
    assert client.get(f"/admin/api/traces/{trace_id}", headers=KEY).status_code == 404


def test_api_without_phoenix_is_503(client, monkeypatch):
    monkeypatch.setattr(traces, "reader", lambda: None)
    res = client.get("/admin/api/traces", headers=KEY)
    assert res.status_code == 503 and "Phoenix" in res.json()["detail"]


@pytest.mark.parametrize("path", ["/admin/api/traces", f"/admin/api/traces/{TRACE_ID}"])
def test_api_when_phoenix_fails_is_502(client, monkeypatch, path):
    reader = StubReader(error=httpx.ConnectError("phoenix down"))
    monkeypatch.setattr(traces, "reader", lambda: reader)
    assert client.get(path, headers=KEY).status_code == 502
