"""
Arize Phoenix backend for ``backend/telemetry.py``: OpenTelemetry spans with
OpenInference semantic conventions, plus span annotations for evals and feedback.

Enabled by ``PHOENIX_COLLECTOR_ENDPOINT`` (the Phoenix base URL, e.g.
``http://mcpokedex-phoenix:6006``), authenticated with ``PHOENIX_API_KEY``. Traces go
to the project ``PHOENIX_PROJECT_NAME`` (default ``mcpokedex-<env>``).

Same tree as Datadog: agent "dexter" → {llm, tool}, emitted by hand from the same
call sites. Strands' native OTel export was evaluated (2026-11): its token counts are
right, but it also exports HTTP/MCP/event-loop spans, roots the trace at the HTTP
span, puts token counts on its agent span (Phoenix would add them twice in the
cumulative counts the token monitor reads) and knows nothing of sessions, setup
failures or feedback ids.

The tracer provider is private, not the global one: Strands and FastAPI emit their
own OTel spans to the global provider and those must not reach Phoenix.

The agent span is never attached to the OTel context: it wraps the SSE async
generator, which may be closed from another context, where detaching fails. Children
find it through a ContextVar and take it as explicit parent.
"""

import json
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
from openinference.semconv.trace import (
    MessageAttributes,
    OpenInferenceMimeTypeValues,
    OpenInferenceSpanKindValues,
    SpanAttributes,
    ToolAttributes,
    ToolCallAttributes,
)
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)

AGENT = OpenInferenceSpanKindValues.AGENT.value
LLM = OpenInferenceSpanKindValues.LLM.value
TOOL = OpenInferenceSpanKindValues.TOOL.value

# Exposed span ids remembered for feedback/evals; same bound as backend/feedback.py.
MAX_IDS = 5000
ANNOTATION_TIMEOUT_S = 5.0

# LiteLLM provider prefix → OpenInference llm.provider, where they differ.
_PROVIDERS = {"gemini": "google"}

# Agent-level token totals go to metadata: Phoenix already sums the llm children.
_TOKEN_KEYS = {
    "input_tokens": SpanAttributes.LLM_TOKEN_COUNT_PROMPT,
    "output_tokens": SpanAttributes.LLM_TOKEN_COUNT_COMPLETION,
    "total_tokens": SpanAttributes.LLM_TOKEN_COUNT_TOTAL,
    "cache_read_input_tokens": SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ,
}


@dataclass
class Span:
    """A live OTel span plus the metadata ``annotate`` has merged into it."""

    otel: Any
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


# The open agent span of this request (set in the SSE generator's context).
_current_agent: ContextVar[Span | None] = ContextVar("phoenix_agent_span", default=None)
_current_session: ContextVar[str | None] = ContextVar("phoenix_session_id", default=None)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _set_value(otel: Any, key: str, mime_key: str, value: Any) -> None:
    if isinstance(value, str):
        otel.set_attribute(key, value)
        otel.set_attribute(mime_key, OpenInferenceMimeTypeValues.TEXT.value)
    else:
        otel.set_attribute(key, _dumps(value))
        otel.set_attribute(mime_key, OpenInferenceMimeTypeValues.JSON.value)


def _set_messages(otel: Any, prefix: str, messages: Any) -> None:
    """``[{role, content, tool_calls?}]`` (the LLMObs message shape) → llm.*_messages.N.message.*"""
    if not isinstance(messages, list):
        return
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        p = f"{prefix}.{i}."
        otel.set_attribute(p + MessageAttributes.MESSAGE_ROLE, str(msg.get("role", "")))
        if msg.get("content"):
            otel.set_attribute(p + MessageAttributes.MESSAGE_CONTENT, str(msg["content"]))
        for j, call in enumerate(msg.get("tool_calls") or []):
            q = f"{p}{MessageAttributes.MESSAGE_TOOL_CALLS}.{j}."
            otel.set_attribute(q + ToolCallAttributes.TOOL_CALL_FUNCTION_NAME, str(call.get("name", "")))
            otel.set_attribute(q + ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON, _dumps(call.get("arguments")))
            if call.get("tool_id"):
                otel.set_attribute(q + ToolCallAttributes.TOOL_CALL_ID, str(call["tool_id"]))


def _tool_schema(tool: dict[str, Any]) -> str:
    """LLMObs tool definition ``{name, description, schema}`` → OpenAI tool JSON, what Phoenix renders."""
    return _dumps(
        {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("schema", {}),
            },
        }
    )


class PhoenixBackend:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        project: str,
        env: str,
        version: str | None,
        processor: SpanProcessor | None = None,
        http: httpx.Client | None = None,
        executor: Any = None,
    ) -> None:
        self.base_url = endpoint.strip().rstrip("/").removesuffix("/v1/traces")
        self.project = project
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self._base_metadata = {"env": env, "version": version or "?"}

        resource = {
            "openinference.project.name": project,
            "service.name": "mcpokedex-agent",
            "deployment.environment": env,
        }
        if version:
            resource["service.version"] = version
        self._provider = TracerProvider(resource=Resource.create(resource))
        if processor is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{self.base_url}/v1/traces", headers=self._headers))
        self._provider.add_span_processor(processor)
        self._tracer = self._provider.get_tracer("mcpokedex.telemetry")

        self._http = http or httpx.Client(timeout=ANNOTATION_TIMEOUT_S)
        # One worker: annotations are rare and order does not matter; never blocks a request.
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="phoenix-annotations")
        self._ids: OrderedDict[str, str] = OrderedDict()
        self._ids_lock = threading.Lock()

    # -- spans ---------------------------------------------------------------

    def _start(self, name: str, kind: str, parent: Span | None, attributes: dict[str, Any]) -> Span:
        # A fresh Context for roots: the current one may hold a FastAPI/Strands span of the
        # global provider, which never reaches Phoenix and would leave the agent span orphaned.
        context = trace.set_span_in_context(parent.otel) if parent else Context()
        session_id = _current_session.get()
        attrs = {SpanAttributes.OPENINFERENCE_SPAN_KIND: kind, **attributes}
        if session_id:
            attrs[SpanAttributes.SESSION_ID] = session_id
        span = Span(self._tracer.start_span(name, context=context, attributes=attrs), kind, dict(self._base_metadata))
        span.otel.set_attribute(SpanAttributes.METADATA, _dumps(span.metadata))
        return span

    def start_agent(self, session_id: str) -> Span:
        _current_session.set(session_id)
        span = self._start("dexter", AGENT, None, {})
        _current_agent.set(span)
        return span

    def start_llm(self, model_id: str) -> Span:
        provider, _, model = model_id.partition("/")
        return self._start(
            "chat",
            LLM,
            _current_agent.get(),
            {SpanAttributes.LLM_MODEL_NAME: model, SpanAttributes.LLM_PROVIDER: _PROVIDERS.get(provider, provider)},
        )

    def start_tool(self, name: str) -> Span:
        return self._start(name, TOOL, _current_agent.get(), {SpanAttributes.TOOL_NAME: name})

    def annotate(
        self,
        span: Span,
        *,
        input_data: Any = None,
        output_data: Any = None,
        metadata: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        tags: dict[str, Any] | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
    ) -> None:
        otel = span.otel
        if input_data is not None:
            _set_value(otel, SpanAttributes.INPUT_VALUE, SpanAttributes.INPUT_MIME_TYPE, input_data)
            if span.kind == LLM:
                _set_messages(otel, SpanAttributes.LLM_INPUT_MESSAGES, input_data)
            elif span.kind == TOOL:
                otel.set_attribute(SpanAttributes.TOOL_PARAMETERS, _dumps(input_data))
        if output_data is not None:
            _set_value(otel, SpanAttributes.OUTPUT_VALUE, SpanAttributes.OUTPUT_MIME_TYPE, output_data)
            if span.kind == LLM:
                _set_messages(otel, SpanAttributes.LLM_OUTPUT_MESSAGES, output_data)
        for i, tool in enumerate(tool_definitions or []):
            otel.set_attribute(f"{SpanAttributes.LLM_TOOLS}.{i}.{ToolAttributes.TOOL_JSON_SCHEMA}", _tool_schema(tool))
        extra = {**(metadata or {}), **(tags or {})}
        if metrics:
            if span.kind == LLM:
                for key, attr in _TOKEN_KEYS.items():
                    if key in metrics:
                        otel.set_attribute(attr, int(metrics[key]))
            else:
                extra.update(metrics)
        if extra:
            span.metadata.update(extra)
            otel.set_attribute(SpanAttributes.METADATA, _dumps(span.metadata))

    def set_error(self, span: Span, exc: BaseException) -> None:
        span.otel.record_exception(exc)
        span.otel.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"[:500]))

    def finish(self, span: Span, *, error: bool = False) -> None:
        if error:
            span.otel.set_status(Status(StatusCode.ERROR))
        span.otel.end()
        if span.kind == AGENT and _current_agent.get() is span:
            _current_agent.set(None)
            _current_session.set(None)

    # -- ids, evals and feedback ---------------------------------------------

    @staticmethod
    def export_ids(span: Span) -> dict[str, str]:
        """OTel ids in the shape ``FeedbackRequest`` accepts: 32-hex trace, decimal span."""
        ctx = span.otel.get_span_context()
        return {"trace_id": format(ctx.trace_id, "032x"), "span_id": str(ctx.span_id)}

    def remember(self, exposed_span_id: str, span: Span) -> None:
        """Tie the span id handed to the frontend (Datadog's when it is on) to this span."""
        with self._ids_lock:
            self._ids[exposed_span_id] = format(span.otel.get_span_context().span_id, "016x")
            self._ids.move_to_end(exposed_span_id)
            while len(self._ids) > MAX_IDS:
                self._ids.popitem(last=False)

    def _lookup(self, exposed_span_id: str) -> str | None:
        with self._ids_lock:
            return self._ids.get(exposed_span_id)

    def submit_evaluations(self, exposed_span_id: str, results: list[Any], checks_version: str) -> None:
        span_id = self._lookup(exposed_span_id)
        if span_id is None:
            return
        self._annotate_async(
            [
                {
                    "span_id": span_id,
                    "name": r.label,
                    "annotator_kind": "CODE",
                    "result": {
                        "label": r.assessment,
                        "score": 1.0 if r.passed else 0.0,
                        "explanation": r.reasoning[:500],
                    },
                    "metadata": {**r.metadata, "eval_source": "rules", "checks_version": checks_version},
                }
                for r in results
            ]
        )

    def submit_feedback(self, exposed_span_id: str, *, value: str, submitter_id: str) -> None:
        span_id = self._lookup(exposed_span_id)
        if span_id is None:
            return
        up = value == "up"
        self._annotate_async(
            [
                {
                    "span_id": span_id,
                    "name": "user_feedback",
                    "annotator_kind": "HUMAN",
                    "result": {"label": "thumbs_up" if up else "thumbs_down", "score": 1.0 if up else 0.0},
                    "identifier": submitter_id,
                    "metadata": {"source": "frontend", "submitter_id": submitter_id},
                }
            ]
        )

    def _annotate_async(self, annotations: list[dict[str, Any]]) -> None:
        # sync=false: Phoenix queues annotations whose span has not arrived yet (evals are
        # sent while the agent span is still open) and inserts them once it lands.
        self._executor.submit(self._post_annotations, {"data": annotations})

    def _post_annotations(self, payload: dict[str, Any]) -> None:
        try:
            resp = self._http.post(
                f"{self.base_url}/v1/span_annotations",
                params={"sync": "false"},
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — telemetry must never break a request
            logger.warning("[TELEMETRY] Phoenix span_annotations falló: %s", exc)

    def shutdown(self) -> None:
        self._provider.shutdown()
