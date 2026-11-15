"""
Finished runs read back from Phoenix, for the admin view at ``/admin/traces``.

Phoenix stores every request as a trace (``backend/telemetry_phoenix.py``) but draws no
picture of how the agent went through it. This module turns Phoenix's REST answers into
the two shapes the view needs (``frontend/src/traces/types.ts``):

- ``list_traces``: the recent queries. ``GET /v1/projects/<p>/traces?include_spans=true``
  gives the token totals and a compact span list per trace (enough to count sub-agents
  and errors); the question the visitor typed comes from the root spans, one more call.
- ``get_trace``: every span of one trace with what it received and answered,
  ``GET /v1/projects/<p>/spans?trace_id=…``.

Same Phoenix and key the agent already writes to (``PHOENIX_COLLECTOR_ENDPOINT``,
``PHOENIX_API_KEY``): no database access, no new secret. ``PHOENIX_PUBLIC_URL`` (the
address people open Phoenix at) adds a link to the trace; blank = no link.
"""

import json
import os
import re
from datetime import datetime
from typing import Any

import httpx
from openinference.semconv.trace import (
    MessageAttributes,
    OpenInferenceSpanKindValues,
    SpanAttributes,
    ToolCallAttributes,
)

from backend import telemetry

TIMEOUT_S = 10.0
# A trace is one request: a root agent, a few model and tool calls, maybe sub-agents.
MAX_SPANS = 1000
MAX_TRACES = 100
# Bounds for what goes to the browser: prompts repeat on every model call.
QUERY_CHARS = 200
TEXT_CHARS = 6000

TRACE_ID_RE = re.compile(r"[0-9a-f]{32}")
# How providers._StreamedOutput.text() writes a tool call into the reply text.
_TOOL_USE_RE = re.compile(r"\[toolUse ([^\]\s]+)\]")

_KINDS = {
    OpenInferenceSpanKindValues.AGENT.value: "agent",
    OpenInferenceSpanKindValues.LLM.value: "llm",
    OpenInferenceSpanKindValues.TOOL.value: "tool",
}
_METADATA = SpanAttributes.METADATA + "."


def _millis(start: str, end: str) -> float:
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return round(delta.total_seconds() * 1000, 1)


def _clip(text: Any, limit: int = TEXT_CHARS) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def _indexed(attributes: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Phoenix flattens lists: ``<prefix>.0.message.role`` → ``[{"message.role": …}]``."""
    items: dict[int, dict[str, Any]] = {}
    for key, value in attributes.items():
        if not key.startswith(prefix + "."):
            continue
        index, _, rest = key[len(prefix) + 1 :].partition(".")
        if index.isdigit():
            items.setdefault(int(index), {})[rest] = value
    return [items[i] for i in sorted(items)]


def _messages(attributes: dict[str, Any], prefix: str) -> list[dict[str, str]]:
    return [
        {
            "role": str(m.get(MessageAttributes.MESSAGE_ROLE, "")),
            "content": _clip(m.get(MessageAttributes.MESSAGE_CONTENT, "")),
        }
        for m in _indexed(attributes, prefix)
    ]


def _tool_calls(attributes: dict[str, Any]) -> list[str]:
    prefix = f"{SpanAttributes.LLM_OUTPUT_MESSAGES}.0.{MessageAttributes.MESSAGE_TOOL_CALLS}"
    names = [call.get(ToolCallAttributes.TOOL_CALL_FUNCTION_NAME) for call in _indexed(attributes, prefix)]
    return [str(name) for name in names if name]


def _json_or_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return _clip(value)
    return parsed if isinstance(parsed, (dict, list)) else _clip(value)


def _error_message(span: dict[str, Any]) -> str | None:
    if span.get("status_code") != "ERROR":
        return None
    if span.get("status_message"):
        return _clip(span["status_message"], 500)
    for event in span.get("events") or []:
        attributes = event.get("attributes") or {}
        if "exception.message" in attributes:
            return _clip(f"{attributes.get('exception.type', 'Error')}: {attributes['exception.message']}", 500)
    return None


def _span(span: dict[str, Any], trace_start: str) -> dict[str, Any]:
    attributes = span.get("attributes") or {}
    kind = _KINDS.get(span.get("span_kind", ""), "tool")
    out: dict[str, Any] = {
        "id": span["context"]["span_id"],
        "parentId": span.get("parent_id"),
        "kind": kind,
        "name": span.get("name", ""),
        "error": span.get("status_code") == "ERROR",
        "startMs": max(_millis(trace_start, span["start_time"]), 0),
        "durationMs": _millis(span["start_time"], span["end_time"]),
    }
    if message := _error_message(span):
        out["errorMessage"] = message
    if metadata := {k[len(_METADATA) :]: v for k, v in attributes.items() if k.startswith(_METADATA)}:
        out["metadata"] = metadata

    input_value = attributes.get(SpanAttributes.INPUT_VALUE)
    output_value = attributes.get(SpanAttributes.OUTPUT_VALUE)
    if kind == "llm":
        out["name"] = attributes.get(SpanAttributes.LLM_MODEL_NAME) or out["name"]
        if SpanAttributes.LLM_TOKEN_COUNT_PROMPT in attributes or SpanAttributes.LLM_TOKEN_COUNT_COMPLETION in attributes:
            out["tokens"] = {
                "prompt": int(attributes.get(SpanAttributes.LLM_TOKEN_COUNT_PROMPT) or 0),
                "completion": int(attributes.get(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION) or 0),
            }
            cached = attributes.get(SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ)
            if cached is not None:
                out["tokens"]["cached"] = int(cached)
        if messages := _messages(attributes, SpanAttributes.LLM_INPUT_MESSAGES):
            out["input"] = messages
        replies = _messages(attributes, SpanAttributes.LLM_OUTPUT_MESSAGES)
        reply = replies[0]["content"] if replies else ""
        # The reply text repeats each tool call as a marker: keep the names, show only the prose.
        if calls := _tool_calls(attributes) or _TOOL_USE_RE.findall(reply):
            out["toolCalls"] = calls
        if text := _TOOL_USE_RE.sub("", reply).strip():
            out["output"] = text
        return out

    if kind == "agent":
        out["name"] = attributes.get(SpanAttributes.AGENT_NAME) or out["name"]
    if input_value is not None:
        out["input"] = _json_or_text(input_value) if kind == "tool" else _clip(input_value)
    if output_value is not None:
        out["output"] = _clip(output_value)
    return out


def _sum_agent_tokens(spans: list[dict[str, Any]]) -> None:
    """Agents carry no token counts (Phoenix sums them itself): add up the model calls under each."""
    by_id = {s["id"]: s for s in spans}
    totals: dict[str, dict[str, int]] = {}
    for span in spans:
        tokens = span.get("tokens")
        if span["kind"] != "llm" or not tokens:
            continue
        parent, seen = span["parentId"], set()
        while parent in by_id and parent not in seen:
            seen.add(parent)
            total = totals.setdefault(parent, {"prompt": 0, "completion": 0})
            total["prompt"] += tokens["prompt"]
            total["completion"] += tokens["completion"]
            parent = by_id[parent]["parentId"]
    for span_id, total in totals.items():
        if by_id[span_id]["kind"] == "agent":
            by_id[span_id]["tokens"] = total


def _summary(trace: dict[str, Any], query: str) -> dict[str, Any]:
    spans = trace.get("spans") or []
    agents = sum(1 for s in spans if s.get("span_kind") == OpenInferenceSpanKindValues.AGENT.value)
    return {
        "traceId": trace["trace_id"],
        "startedAt": trace["start_time"],
        "query": query,
        "durationMs": _millis(trace["start_time"], trace["end_time"]),
        "tokens": int(trace.get("token_count_total") or 0),
        "error": any(s.get("status_code") == "ERROR" for s in spans),
        "spanCount": len(spans),
        "subAgents": max(agents - 1, 0),
    }


class PhoenixReader:
    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        project: str,
        public_url: str = "",
        http: httpx.Client | None = None,
    ) -> None:
        self._project_url = f"{base_url}/v1/projects/{project}"
        self._headers = headers
        self._public_url = public_url.strip().rstrip("/")
        self._http = http or httpx.Client(timeout=TIMEOUT_S)
        self._project_id: str | None = None

    def _get(self, path: str = "", **params: Any) -> Any:
        response = self._http.get(self._project_url + path, params=params, headers=self._headers)
        response.raise_for_status()
        return response.json()["data"]

    def _trace_link(self, trace_id: str) -> str | None:
        """The trace in Phoenix's own UI, which addresses a project by its id, not its name."""
        if not self._public_url:
            return None
        if self._project_id is None:
            self._project_id = self._get()["id"]
        return f"{self._public_url}/projects/{self._project_id}/traces/{trace_id}"

    def list_traces(self, limit: int = 30) -> list[dict[str, Any]]:
        """The most recent queries first."""
        traces = self._get("/traces", limit=min(limit, MAX_TRACES), sort="start_time", order="desc", include_spans="true")
        if not traces:
            return []
        roots = self._get(
            "/spans", trace_id=[t["trace_id"] for t in traces], parent_id="null", limit=min(len(traces) * 2, MAX_SPANS)
        )
        queries = {
            r["context"]["trace_id"]: _clip((r.get("attributes") or {}).get(SpanAttributes.INPUT_VALUE, ""), QUERY_CHARS)
            for r in roots
        }
        return [_summary(t, queries.get(t["trace_id"], "")) for t in traces]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """One query with every step, or None when Phoenix has no such trace."""
        raw = self._get("/spans", trace_id=[trace_id], limit=MAX_SPANS, sort="start_time", order="asc")
        if not raw:
            return None
        start = min(s["start_time"] for s in raw)
        end = max(s["end_time"] for s in raw)
        spans = [_span(s, start) for s in raw]
        _sum_agent_tokens(spans)
        # A span whose parent Phoenix has not stored (yet) would have no place in the view.
        known = {s["id"] for s in spans}
        roots = [s for s in spans if s["parentId"] not in known] or spans
        root = next((s for s in roots if s["kind"] == "agent"), roots[0])
        tokens = root.get("tokens") or {"prompt": 0, "completion": 0}
        return {
            "traceId": trace_id,
            "startedAt": start,
            "query": _clip(root.get("input", ""), QUERY_CHARS) if isinstance(root.get("input"), str) else "",
            "durationMs": _millis(start, end),
            "tokens": tokens["prompt"] + tokens["completion"],
            "error": any(s["error"] for s in spans),
            "spanCount": len(spans),
            "subAgents": max(sum(1 for s in spans if s["kind"] == "agent") - 1, 0),
            "spans": spans,
            "phoenixUrl": self._trace_link(trace_id),
        }


# (telemetry's Phoenix backend, the reader built for it)
_cached: tuple[Any, PhoenixReader] | None = None


def reader() -> PhoenixReader | None:
    """The reader over the Phoenix that telemetry writes to; None when Phoenix is off."""
    global _cached
    backend = telemetry.phoenix_backend()
    if backend is None:
        return None
    if _cached is None or _cached[0] is not backend:
        _cached = (
            backend,
            PhoenixReader(
                base_url=backend.base_url,
                headers=backend.headers,
                project=backend.project,
                public_url=os.environ.get("PHOENIX_PUBLIC_URL", ""),
            ),
        )
    return _cached[1]
