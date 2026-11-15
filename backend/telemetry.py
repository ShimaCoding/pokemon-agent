"""
Agent tracing to Datadog LLM Observability and/or Arize Phoenix.

Each backend is enabled on its own: Datadog with ``DD_API_KEY``, Phoenix with
``PHOENIX_COLLECTOR_ENDPOINT`` (see ``backend/telemetry_phoenix.py``). Both can run at
once, to compare them while the Datadog trial lasts. Every request becomes one trace:

    agent "dexter" (session = conversation_id)
    ├── llm    one per model call: messages, tool definitions, output, tokens (incl. cached), model
    ├── tool   one per tool call: input and result
    └── agent  one per sub-agent run (``sub_agent_span`` / ``SubAgentTrace``), with its own
               llm, tool and agent children

Agents nest: llm, tool and sub-agent spans hang from the nearest agent span still open
in the current context (a ContextVar, so asyncio tasks and ``asyncio.to_thread`` each
see their own). Code that starts its own thread must copy the context
(``contextvars.copy_context().run``), or its spans lose their agent. A sub-agent is
never a root: outside a request it gets no span. Agent spans are tagged
``agent_level:root|sub``; monitors count only the roots (one per user query). An
agent used as a tool has no tool span, its agent span already has the input, the
output and the latency (``main._ToolTrace``).

After each reply the rule-based quality checks (``backend/evals/checks.py``) are
attached to the agent span as evaluations (``submit_evaluations``), and the user's
👍/👎 as feedback (``submit_feedback``). In Phoenix both are span annotations.

Datadog spans are sent agentless with the ddtrace LLM Observability SDK, straight to
Datadog's intake; nothing runs on the host. Without either backend every helper here
is a no-op, and telemetry never breaks a request.

Why not Strands' native OpenTelemetry export: Datadog documents it, but with
this org the OTLP spans only ever reached APM, never LLM Observability, while
SDK spans show up fine (for Phoenix, see telemetry_phoenix.py). The spans are emitted
from the places that already see everything: ``TracedLiteLLMModel.stream`` (llm),
``_ToolTrace`` (tool) and the SSE generator (agent). ddtrace's auto-instrumentation
stays off: its LiteLLM integration reports 0 tokens for streamed calls and does not
cover tools.

Spans carry ``env`` (``DD_ENV``, default ``local``; docker-compose.yml pins
``prod``; it names the env of both backends) and ``version`` (the git commit, see
``backend/build_info.py``), so deploys can be compared side by side.
"""

import asyncio
import logging
import os
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, ContextManager, Iterator

from backend import build_info

logger = logging.getLogger(__name__)

# ml_app in Datadog (shown lowercased). Matches the app created in the Datadog onboarding.
DEFAULT_ML_APP = "McpokedexAgent"

# Datadog `env` tag when DD_ENV is unset. Prod sets DD_ENV explicitly in docker-compose.yml.
DEFAULT_ENV = "local"

# Name of the root agent span: dashboards, monitors and eval datasets group by it.
DEFAULT_AGENT_NAME = "dexter"

# Phoenix project is "<this>-<env>" unless PHOENIX_PROJECT_NAME is set.
DEFAULT_PHOENIX_PROJECT = "mcpokedex"

# Pure SDK mode: no APM tracer, no Datadog Agent on localhost:8126.
_DDTRACE_DEFAULTS = {
    "DD_TRACE_ENABLED": "false",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
}

_dd_enabled = False
_phoenix: Any = None  # telemetry_phoenix.PhoenixBackend when PHOENIX_COLLECTOR_ENDPOINT is set

# The browser going away mid-stream is not an agent failure.
_CLIENT_GONE = (GeneratorExit, asyncio.CancelledError)


@dataclass(eq=False)
class _Span:
    """One span in every enabled backend: ``dd`` is the ddtrace span, ``px`` the Phoenix one."""

    dd: Any = None
    px: Any = None
    # Agent spans only: the agent this one runs under, and whether it has finished.
    parent: "_Span | None" = None
    ended: bool = False


# The innermost agent span started in this context. It may already be finished: see _open_agent.
_current_agent: ContextVar[_Span | None] = ContextVar("telemetry_agent_span", default=None)


def _open_agent() -> _Span | None:
    """The nearest agent span still open in this context; children hang from it.

    Walks past finished spans: an agent closed from another context (the SSE generator
    closed by Starlette, a sub-agent finished by another task) could not reset this one.
    """
    span = _current_agent.get()
    while span is not None and span.ended:
        span = span.parent
    return span


def _open_px() -> Any:
    agent = _open_agent()
    return agent.px if agent is not None else None


def _env() -> str:
    return os.environ.get("DD_ENV", "").strip() or DEFAULT_ENV


def setup_telemetry() -> bool:
    """Enable each backend whose env vars are set. Returns True when any is enabled."""
    _setup_datadog()
    _setup_phoenix()
    if not is_enabled():
        logger.info("[TELEMETRY] Desactivada (sin DD_API_KEY ni PHOENIX_COLLECTOR_ENDPOINT)")
    return is_enabled()


def _setup_phoenix() -> bool:
    global _phoenix
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
    if not endpoint:
        return False
    from backend.telemetry_phoenix import PhoenixBackend

    env = _env()
    version = build_info.version()
    project = os.environ.get("PHOENIX_PROJECT_NAME", "").strip() or f"{DEFAULT_PHOENIX_PROJECT}-{env}"
    _phoenix = PhoenixBackend(
        endpoint=endpoint,
        api_key=os.environ.get("PHOENIX_API_KEY", "").strip(),
        project=project,
        env=env,
        version=version,
    )
    logger.info(
        "[TELEMETRY] Phoenix → %s (project=%s env=%s version=%s)", _phoenix.base_url, project, env, version or "?"
    )
    return True


def _setup_datadog() -> bool:
    """LLM Observability if ``DD_API_KEY`` is set."""
    global _dd_enabled
    api_key = os.environ.get("DD_API_KEY", "").strip()
    if not api_key:
        return False

    for name, value in _DDTRACE_DEFAULTS.items():
        os.environ.setdefault(name, value)

    version = build_info.version()
    sha = build_info.commit_sha()
    if sha:  # git.commit.sha / repository_url tags; ddtrace reads these at import time
        os.environ.setdefault("DD_GIT_COMMIT_SHA", sha)
        os.environ.setdefault("DD_GIT_REPOSITORY_URL", build_info.REPOSITORY_URL)

    import ddtrace
    from ddtrace.llmobs import LLMObs

    if version:
        # LLMObs.enable() has no `version` param; spans read ddtrace.config.version per span.
        ddtrace.config.version = version
    env = _env()
    ml_app = os.environ.get("DD_LLMOBS_ML_APP", DEFAULT_ML_APP)
    site = os.environ.get("DD_SITE", "datadoghq.com")
    LLMObs.enable(
        ml_app=ml_app,
        api_key=api_key,
        site=site,
        agentless_enabled=True,
        integrations_enabled=False,
        env=env,
        service=ml_app,
    )
    _dd_enabled = True
    logger.info(
        "[TELEMETRY] LLM Observability → %s (ml_app=%s env=%s version=%s)", site, ml_app, env, version or "?"
    )
    return True


def is_enabled() -> bool:
    return _dd_enabled or _phoenix is not None


def _px(method: str, *args: Any, **kwargs: Any) -> Any:
    """Call the Phoenix backend; None (and a warning) instead of raising."""
    if _phoenix is None:
        return None
    try:
        return getattr(_phoenix, method)(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break a request
        logger.warning("[TELEMETRY] Phoenix %s falló: %s", method, exc)
        return None


def _set_error(span: _Span, exc: BaseException) -> None:
    if span.dd is not None:
        span.dd.set_exc_info(type(exc), exc, exc.__traceback__)
    if span.px is not None:
        _px("set_error", span.px, exc)


def _start_agent(name: str, *, session_id: str | None = None, parent: _Span | None = None) -> _Span:
    """Open an agent span and make it the current one: the root of a trace, or a sub-agent of ``parent``."""
    span = _Span(parent=parent)
    if _dd_enabled:
        from ddtrace.llmobs import LLMObs

        # A sub-agent takes no session: ddtrace nests it under its active span (the parent
        # agent, also tracked with contextvars) and LLM Observability inherits the session.
        span.dd = LLMObs.agent(name=name, session_id=session_id) if parent is None else LLMObs.agent(name=name)
    span.px = _px("start_agent", name, session_id=session_id, parent=parent.px if parent is not None else None)
    _current_agent.set(span)
    annotate(span, tags={"agent_level": "root" if parent is None else "sub"})
    return span


def _finish_agent(span: _Span, *, error: bool = False) -> None:
    try:
        if span.dd is not None:
            if error:
                span.dd.error = 1
            span.dd.finish()
        if span.px is not None:
            _px("finish", span.px, error=error)
    finally:
        span.ended = True
        # Not ContextVar.reset(token): it raises when the span is closed from another context.
        current = _current_agent.get()
        if current is not None and current.ended:
            _current_agent.set(_open_agent())


@contextmanager
def _agent_span(session_id: str, name: str) -> Iterator[_Span]:
    span = _start_agent(name, session_id=session_id)
    try:
        yield span
    except _CLIENT_GONE:
        annotate(span, tags={"client_disconnected": "true"})
        raise
    except BaseException as exc:
        _set_error(span, exc)
        raise
    finally:
        _finish_agent(span)


def agent_span(session_id: str, *, name: str = DEFAULT_AGENT_NAME) -> ContextManager[Any]:
    """Root span for one user turn; nullcontext (yields None) when disabled.

    Errors mark the span (status:error, what the error-rate monitor counts);
    a client disconnect does not.
    """
    if not is_enabled():
        return nullcontext()
    return _agent_span(session_id, name)


def start_sub_agent_span(name: str) -> Any:
    """Start the span of a sub-agent under the agent open in this context.

    None when telemetry is off or no agent is open: a sub-agent never starts a trace, so
    monitors and alerts keep counting one root agent span per user query. Finish it with
    ``finish_sub_agent_span``. Never raises.
    """
    if not is_enabled():
        return None
    parent = _open_agent()
    if parent is None:
        return None
    try:
        return _start_agent(name, parent=parent)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break a request
        logger.warning("[TELEMETRY] start_sub_agent_span falló: %s", exc)
        return None


def finish_sub_agent_span(
    span: Any,
    *,
    input_data: Any = None,
    output_data: Any = None,
    metrics: dict[str, Any] | None = None,
    error: bool = False,
) -> None:
    """Close a sub-agent span. Token ``metrics`` go to its metadata in Phoenix, which sums the llm children."""
    if span is None:
        return
    fields = {"input_data": input_data, "output_data": output_data, "metrics": metrics}
    fields = {key: value for key, value in fields.items() if value is not None}
    try:
        if fields:
            annotate(span, **fields)
        _finish_agent(span, error=error)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break a request
        logger.warning("[TELEMETRY] finish_sub_agent_span falló: %s", exc)


@contextmanager
def _sub_agent_span(name: str) -> Iterator[_Span | None]:
    span = start_sub_agent_span(name)
    try:
        yield span
    except _CLIENT_GONE:
        raise
    except BaseException as exc:
        if span is not None:
            _set_error(span, exc)
        raise
    finally:
        finish_sub_agent_span(span)


def sub_agent_span(name: str) -> ContextManager[Any]:
    """``with`` form of ``start_sub_agent_span``; yields None when there is no span.

    An exception marks only this span: the parent agent is marked if it lets it propagate.
    """
    if not is_enabled():
        return nullcontext()
    return _sub_agent_span(name)


# Strands usage keys → the metric names ``annotate`` takes.
_USAGE_METRICS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "totalTokens": "total_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
}


def _last_user_text(messages: Any) -> str | None:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            text = " ".join(block["text"] for block in message.get("content", []) if "text" in block)
            if text:
                return text
    return None


class SubAgentTrace:
    """Strands HookProvider: an agent span for each run of the sub-agent it is registered on.

    Works for an agent used as a tool (``Agent.as_tool()``) and as a Swarm or Graph node:
    the invocation hooks fire inside the agent's own ``stream_async``, in the task that
    runs it, so the span hangs from the agent that called it and the sub-agent's llm and
    tool spans hang from the span. Give the agent a ``name`` (the span and graph node
    name) and put this hook first, so the span opens before the other hooks run and
    closes after them (After callbacks run in reverse order).

    One span per instance at a time: Strands does not run one Agent concurrently.
    """

    def __init__(self) -> None:
        self._span: Any = None

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import AfterInvocationEvent, BeforeInvocationEvent

        registry.add_callback(BeforeInvocationEvent, self._on_start)
        registry.add_callback(AfterInvocationEvent, self._on_end)

    def _on_start(self, event) -> None:
        self._span = start_sub_agent_span(event.agent.name)
        if self._span is None:
            return
        try:
            if text := _last_user_text(event.messages):
                annotate(self._span, input_data=text)
        except Exception as exc:  # noqa: BLE001 — telemetry must never break a request
            logger.warning("[TELEMETRY] SubAgentTrace falló: %s", exc)

    def _on_end(self, event) -> None:
        span, self._span = self._span, None
        if span is None:
            return
        result = event.result
        output, metrics = None, None
        try:
            if result is not None:
                output = str(result).strip() or None
                usage = result.metrics.accumulated_usage or {}
                metrics = {name: usage[key] for key, name in _USAGE_METRICS.items() if key in usage} or None
        except Exception as exc:  # noqa: BLE001 — telemetry must never break a request
            logger.warning("[TELEMETRY] SubAgentTrace falló: %s", exc)
        # No result: the run raised, was cancelled or a hook denied it. Sub-agent spans feed
        # no monitor, so the approximation is fine.
        finish_sub_agent_span(span, output_data=output, metrics=metrics, error=result is None)


def record_failed_request(session_id: str, exc: BaseException, *, input_data: Any = None) -> None:
    """Error agent span for a turn that failed before the agent ran (e.g. MCP server down).

    Without it those failures have no span and the error-rate monitor reads 0%.
    """
    if not is_enabled():
        return
    try:
        with agent_span(session_id) as span:
            annotate(span, input_data=input_data, tags={"failure_stage": "setup"})
            _set_error(span, exc)
    except Exception as err:  # noqa: BLE001 — telemetry must never break a request
        logger.warning("[TELEMETRY] record_failed_request falló: %s", err)


@contextmanager
def _llm_span(model_id: str) -> Iterator[_Span]:
    provider, _, model = model_id.partition("/")
    if _dd_enabled:
        from ddtrace.llmobs import LLMObs

        dd_span = LLMObs.llm(model_name=model, model_provider=provider, name="chat")
    else:
        dd_span = nullcontext()
    # ddtrace's context manager marks errors on its own span.
    with dd_span as dd:
        span = _Span(dd=dd, px=_px("start_llm", model_id, _open_px()))
        try:
            yield span
        except _CLIENT_GONE:
            raise
        except BaseException as exc:
            if span.px is not None:
                _px("set_error", span.px, exc)
            raise
        finally:
            if span.px is not None:
                _px("finish", span.px)


def llm_span(model_id: str) -> ContextManager[Any]:
    """Span for one model call. ``model_id`` is LiteLLM's ``provider/model``."""
    if not is_enabled():
        return nullcontext()
    return _llm_span(model_id)


def start_tool_span(name: str) -> Any:
    """Start a tool span (activated in ddtrace); finish it with ``finish_tool_span``."""
    if not is_enabled():
        return None
    span = _Span()
    if _dd_enabled:
        from ddtrace.llmobs import LLMObs

        span.dd = LLMObs.tool(name=name)
    span.px = _px("start_tool", name, _open_px())
    return span


def finish_tool_span(
    span: Any, *, input_data: Any, output_data: Any, error: bool = False, tags: dict[str, str] | None = None
) -> None:
    if span is None:
        return
    if tags:
        annotate(span, input_data=input_data, output_data=output_data, tags=tags)
    else:
        annotate(span, input_data=input_data, output_data=output_data)
    if span.dd is not None:
        if error:
            span.dd.error = 1
        span.dd.finish()
    if span.px is not None:
        _px("finish", span.px, error=error)


def annotate(span: Any, **fields: Any) -> None:
    """LLMObs.annotate (and its Phoenix equivalent) that never breaks a request because of telemetry."""
    if span is None:
        return
    if span.dd is not None:
        from ddtrace.llmobs import LLMObs

        try:
            LLMObs.annotate(span=span.dd, **fields)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TELEMETRY] annotate falló: %s", exc)
    if span.px is not None:
        _px("annotate", span.px, **fields)


def _export_dd_ids(dd_span: Any) -> dict[str, str] | None:
    from ddtrace.llmobs import LLMObs

    try:
        exported = LLMObs.export_span(span=dd_span)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TELEMETRY] export_span falló: %s", exc)
        return None
    if not exported:
        return None
    return {"trace_id": exported["trace_id"], "span_id": exported["span_id"]}


def export_ids(span: Any) -> dict[str, str] | None:
    """``{trace_id, span_id}`` of the agent span, to attach evals and feedback; None when disabled.

    Datadog's ids when it is on (Phoenix maps them to its own span), else Phoenix's.
    Both fit ``FeedbackRequest``: 32-hex trace id, decimal 64-bit span id.
    """
    if span is None or not is_enabled():
        return None
    ids = _export_dd_ids(span.dd) if span.dd is not None else None
    if span.px is not None:
        if ids is None:
            ids = _px("export_ids", span.px)
        if ids:
            _px("remember", ids["span_id"], span.px)
    return ids


def submit_evaluations(ids: dict[str, str] | None, results: list[Any]) -> None:
    """Rule-check results (``backend.evals.checks.EvalResult``) as boolean evals on the agent span.

    Only enqueues: ddtrace's eval writer and the Phoenix annotation worker send them in the background.
    """
    if not ids or not is_enabled():
        return
    from backend.evals.checks import CHECKS_VERSION

    _px("submit_evaluations", ids["span_id"], results, CHECKS_VERSION)
    if not _dd_enabled:
        return
    from ddtrace.llmobs import LLMObs

    for r in results:
        try:
            LLMObs.submit_evaluation(
                label=r.label,
                metric_type="boolean",
                value=r.passed,
                span={"span_id": ids["span_id"], "trace_id": ids["trace_id"]},
                assessment=r.assessment,
                reasoning=r.reasoning[:500],
                metadata=r.metadata,
                tags={"eval_source": "rules", "checks_version": CHECKS_VERSION},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TELEMETRY] submit_evaluation %s falló: %s", r.label, exc)


def submit_feedback(ids: dict[str, str], *, value: str, submitter_id: str) -> None:
    """A user's 👍/👎 (``value`` "up"/"down") as feedback on the agent span (label ``user_feedback``)."""
    if not is_enabled():
        return
    _px("submit_feedback", ids["span_id"], value=value, submitter_id=submitter_id)
    if not _dd_enabled:
        return
    from ddtrace.llmobs import LLMObs

    up = value == "up"
    try:
        LLMObs.submit_feedback(
            label="user_feedback",
            metric_type="categorical",
            value="thumbs_up" if up else "thumbs_down",
            assessment="pass" if up else "fail",
            submitter={"id": submitter_id, "type": "user"},
            span={"span_id": ids["span_id"], "trace_id": ids["trace_id"]},
            tags={"source": "frontend"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TELEMETRY] submit_feedback falló: %s", exc)
