"""observability/grafana/dashboards/mcpokedex-agent-graph.json: the execution graph, pinned to the code.

Two Node graph panels over Phoenix's Postgres: the agents/models/tools map of the dashboard
range and the span tree of one trace. These checks keep the SQL in sync with what
telemetry_phoenix.py emits and with what Grafana's Node graph needs: frames named ``nodes``
and ``edges``, no edge that points at a node the other query does not return (the panel
crashes on those), and node positions from the query (``fixedx``/``fixedy``, centred on the
origin), because Grafana 11.3 draws at scale 1 around (0, 0) with no fit-to-panel and has no
per-panel layered layout.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from openinference.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.telemetry_phoenix import PhoenixBackend

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = ROOT / "observability" / "grafana" / "dashboards"
DATASOURCE_UID = "phoenix-pg"
UID = "mcpokedex-agent-graph"
ROOT_AGENT = "s.parent_id IS NULL AND s.span_kind = 'AGENT'"

NODE_FIELDS = {"id", "title", "subtitle", "mainstat", "secondarystat", "arc__ok", "arc__error"}
EDGE_FIELDS = {"id", "source", "target", "mainstat"}


def _dashboard(name: str = UID) -> dict:
    return json.loads((DASHBOARDS / f"{name}.json").read_text(encoding="utf-8"))


def _json_path(attribute: str, *more: str) -> str:
    """How Phoenix stores an OpenInference attribute: ``llm.model_name`` → ``'{llm,model_name}'``."""
    return "'{" + ",".join([*attribute.split("."), *more]) + "}'"


def _graphs() -> dict[str, dict[str, str]]:
    """Panel title → {refId: SQL} of the Node graph panels."""
    return {
        p["title"]: {t["refId"]: t["rawSql"] for t in p["targets"]}
        for p in _dashboard()["panels"]
        if p["type"] == "nodeGraph"
    }


def _cte(sql: str) -> str:
    """The WITH clause: everything before the first top-level SELECT."""
    return sql[: re.search(r"^SELECT ", sql, re.M).start()]


def _aliases(sql: str) -> set[str]:
    return set(re.findall(r"\bAS (\w+)", sql[len(_cte(sql)) :]))


def _spans_cte(sql: str) -> str:
    """The materialized CTE that reads the spans table; the rest of the query builds on it."""
    return re.search(r"\w+ AS MATERIALIZED \(\n.*?\n\)", sql, re.S).group()


@pytest.fixture(params=["Mapa de agentes", "Recorrido de una consulta"])
def graph(request) -> dict[str, str]:
    return _graphs()[request.param]


def test_dashboard_reads_only_the_phoenix_datasource():
    dash = _dashboard()
    assert dash["uid"] == UID
    targets = [t for p in dash["panels"] for t in p["targets"]]
    assert targets and {t["datasource"]["uid"] for t in targets} == {DATASOURCE_UID}
    assert all(t["rawQuery"] is True and t["format"] == "table" for t in targets)
    assert all("$project" in t["rawSql"] for t in targets)
    # Tables grafana_ro can read (grafana-ro.sql); nothing else may be queried.
    tables = {name for t in targets for name in re.findall(r"(?<!epoch )\b(?:FROM|JOIN) ([a-z_]+)\b", t["rawSql"])}
    ctes = {name for t in targets for name in re.findall(r"\b(\w+) AS (?:MATERIALIZED )?\(\n", t["rawSql"])}
    assert tables - ctes == {"spans", "traces", "projects"}


def test_two_node_graphs():
    assert list(_graphs()) == ["Mapa de agentes", "Recorrido de una consulta"]


def test_node_graph_frames_and_fields(graph):
    # Grafana tells the frames apart by refId.
    assert set(graph) == {"nodes", "edges"}
    assert NODE_FIELDS <= _aliases(graph["nodes"])
    assert EDGE_FIELDS <= _aliases(graph["edges"])
    assert "source" not in _aliases(graph["nodes"])  # a `source` field would make it an edges frame
    # No fit-to-panel in Grafana 11.3: the query places every node.
    assert {"fixedx", "fixedy"} <= _aliases(graph["nodes"])


def test_nodes_and_edges_read_the_same_spans(graph):
    # An edge to a node missing from the nodes query crashes the panel: both must build on the
    # same scan of the spans table, and read it nowhere else.
    assert _spans_cte(graph["nodes"]) == _spans_cte(graph["edges"])
    for sql in graph.values():
        assert sql.count("FROM spans") == 1 and "FROM spans" in _spans_cte(sql)


def test_arcs_are_colored():
    for panel in _dashboard()["panels"]:
        if panel["type"] == "nodeGraph":
            assert panel["options"]["nodes"]["arcs"] == [
                {"field": "arc__ok", "color": "green"},
                {"field": "arc__error", "color": "red"},
            ]


def test_map_aggregates_by_what_telemetry_emits():
    sql = _graphs()["Mapa de agentes"]["nodes"]
    # A trace is in or out as a whole, so no edge loses its parent at the edge of the range.
    assert "$__timeFilter(t.start_time)" in sql
    # Spans from before agent.name existed fall back to the span name ("dexter").
    assert f"'agent:' || coalesce(s.attributes #>> {_json_path(SpanAttributes.AGENT_NAME)}, s.name)" in sql
    assert f"'llm:' || coalesce(s.attributes #>> {_json_path(SpanAttributes.LLM_MODEL_NAME)}, s.name)" in sql
    edges = _graphs()["Mapa de agentes"]["edges"]
    assert "JOIN sp pa ON pa.trace_rowid = ch.trace_rowid AND pa.span_id = ch.parent_id" in edges
    assert "GROUP BY pa.node, ch.node" in edges
    # Columns: root agents, sub-agents (by the parent agent telemetry stamps on them), models, tools.
    assert f"s.attributes #>> {_json_path(SpanAttributes.GRAPH_NODE_PARENT_ID)} AS parent_agent" in sql
    # A recursive CTE here makes Postgres JIT-compile the query: 2 s for 100 spans.
    assert "RECURSIVE" not in sql and "RECURSIVE" not in edges


def test_trace_view_shows_one_trace_with_fixed_positions():
    graph = _graphs()["Recorrido de una consulta"]
    for sql in graph.values():
        assert "t.trace_id = '$trace_id' AND p.name = '$project'" in sql
        assert "$__timeFilter" not in sql  # the trace is shown whole, whatever the range
    nodes = graph["nodes"]
    assert f"attributes #>> {_json_path(SpanAttributes.AGENT_NAME)}" in nodes
    assert f"attributes #>> {_json_path(SpanAttributes.LLM_MODEL_NAME)}" in nodes
    assert f"attributes #>> {_json_path(SpanAttributes.INPUT_VALUE)}" in nodes
    assert f"attributes #>> {_json_path(SpanAttributes.METADATA, 'cache')}" in nodes
    assert _cte(graph["nodes"]) == _cte(graph["edges"])


def test_sibling_sequence_edges_can_be_switched_off():
    edges = _graphs()["Recorrido de una consulta"]["edges"]
    parent_child, sequence = edges[len(_cte(edges)) :].split("UNION ALL")
    assert "JOIN sp pa ON pa.span_id = ch.parent_id" in parent_child
    assert "PARTITION BY parent_id ORDER BY start_time" in sequence
    assert "'$secuencia' = 'on'" in sequence and "'$secuencia'" not in parent_child
    assert {"color", "strokedasharray"} <= _aliases(parent_child)  # the sequence rows fill them by position


def test_variables():
    variables = {v["name"]: v for v in _dashboard()["templating"]["list"]}
    assert list(variables) == ["project", "trace_id", "secuencia"]
    assert variables["project"] == {v["name"]: v for v in _dashboard("mcpokedex-phoenix")["templating"]["list"]}["project"]

    trace = variables["trace_id"]
    sql = trace["query"]
    # One option per user query: root agent spans, never sub-agents.
    assert ROOT_AGENT in sql and "$project" in sql and "$__timeFilter(s.start_time)" in sql
    assert re.search(r"LIMIT \d+$", sql)
    assert {"__value", "__text"} <= set(re.findall(r"\bAS (\w+)", sql))
    assert trace["refresh"] == 2  # on time range change

    assert variables["secuencia"]["query"] == "on,off"
    assert variables["secuencia"]["current"]["value"] == "on"


def test_recent_queries_link_to_the_trace_view_and_to_phoenix():
    [table] = [p for p in _dashboard()["panels"] if p["type"] == "table"]
    sql = table["targets"][0]["rawSql"]
    assert ROOT_AGENT in sql and "$__timeFilter(s.start_time)" in sql
    links = {
        o["matcher"]["options"]: o["properties"][0]["value"][0]["url"]
        for o in table["fieldConfig"]["overrides"]
        if o["properties"][0]["id"] == "links"
    }
    assert links["consulta"].startswith(f"/d/{UID}?") and "var-trace_id=${__data.fields.trace_id}" in links["consulta"]
    assert "${__url_time_range}" in links["consulta"]
    # Phoenix addresses a project by its relay id: base64("Project:<id>").
    assert "encode(convert_to('Project:' || p.id, 'UTF8'), 'base64') AS phoenix_project" in sql
    assert links["trace_id"] == (
        "https://phoenix.shimadev.xyz/projects/${__data.fields.phoenix_project}/traces/${__value.raw}"
    )


def test_phoenix_root_span_links_to_this_dashboard():
    # Phoenix → Grafana: telemetry_phoenix.py writes metadata.grafana_url on the root agent span.
    # GRAFANA_AGENT_GRAPH_URL holds the dashboard URL (with the uid); the code adds the variables.
    dash = _dashboard()
    example = re.search(r"^GRAFANA_AGENT_GRAPH_URL=.*?(https://\S+?);", (ROOT / ".env.example").read_text(), re.M).group(1)
    assert urlsplit(example).path == f"/d/{dash['uid']}"

    exporter = InMemorySpanExporter()
    backend = PhoenixBackend(
        endpoint="http://phoenix:6006",
        api_key="",
        project="mcpokedex-prod",
        env="prod",
        version=None,
        agent_graph_url=example,
        processor=SimpleSpanProcessor(exporter),
    )
    root = backend.start_agent("dexter", session_id="conv-12345678")
    backend.finish(root)
    backend.shutdown()
    [span] = exporter.get_finished_spans()
    url = urlsplit(json.loads(span.attributes[SpanAttributes.METADATA])["grafana_url"])
    query = parse_qs(url.query)

    assert url.path == f"/d/{dash['uid']}"
    variables = {v["name"]: v for v in dash["templating"]["list"]}
    assert {k.removeprefix("var-") for k in query if k.startswith("var-")} == {"project", "trace_id"} <= set(variables)
    assert query["var-project"] == [variables["project"]["current"]["value"]]
    # $trace_id holds traces.trace_id: 32 hex, as OTel writes it.
    assert query["var-trace_id"] == [format(span.context.trace_id, "032x")]
    assert "t.trace_id AS __value" in variables["trace_id"]["query"]
    # Its options are the root agent spans of the range: the link's range must hold this one.
    assert "$__timeFilter(s.start_time)" in variables["trace_id"]["query"]
    assert int(query["from"][0]) < span.start_time // 1_000_000 < int(query["to"][0])


def test_dashboards_link_to_each_other():
    def urls(name: str) -> list[str]:
        return [link["url"] for link in _dashboard(name)["links"]]

    assert "/d/mcpokedex-phoenix" in urls(UID)
    assert f"/d/{UID}" in urls("mcpokedex-phoenix")
