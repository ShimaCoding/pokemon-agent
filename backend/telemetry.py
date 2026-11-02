"""
Datadog LLM Observability (Agent Observability) for the agent.

With ``DD_API_KEY`` set, every request becomes one trace in Datadog:

    agent "dexter" (session = conversation_id)
    ├── llm   one per model call: messages, output, tokens (incl. cached), model
    └── tool  one per tool call: input and result

After each reply the rule-based quality checks (``backend/evals/checks.py``) are
attached to the agent span as evaluations (``submit_evaluations``).

Spans are sent agentless with the ddtrace LLM Observability SDK, straight to
Datadog's intake; nothing runs on the host. Without the key every helper here is
a no-op.

Why not Strands' native OpenTelemetry export: Datadog documents it, but with
this org the OTLP spans only ever reached APM, never LLM Observability, while
SDK spans show up fine. The spans are emitted from the places that already see
everything: ``TracedLiteLLMModel.stream`` (llm), ``_ToolTrace`` (tool) and the
SSE generator (agent). ddtrace's auto-instrumentation stays off: its LiteLLM
integration reports 0 tokens for streamed calls and does not cover tools.

Spans carry ``env`` (``DD_ENV``, default ``local``; docker-compose.yml pins
``prod``) and ``version`` (the git commit, see ``backend/build_info.py``), so
deploys can be compared side by side.
"""

import asyncio
import logging
import os
from contextlib import contextmanager, nullcontext
from typing import Any, ContextManager, Iterator

from backend import build_info

logger = logging.getLogger(__name__)

# ml_app in Datadog (shown lowercased). Matches the app created in the Datadog onboarding.
DEFAULT_ML_APP = "McpokedexAgent"

# Datadog `env` tag when DD_ENV is unset. Prod sets DD_ENV explicitly in docker-compose.yml.
DEFAULT_ENV = "local"

# Pure SDK mode: no APM tracer, no Datadog Agent on localhost:8126.
_DDTRACE_DEFAULTS = {
    "DD_TRACE_ENABLED": "false",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
}

_enabled = False

# The browser going away mid-stream is not an agent failure.
_CLIENT_GONE = (GeneratorExit, asyncio.CancelledError)


def setup_telemetry() -> bool:
    """Enable LLM Observability if ``DD_API_KEY`` is set. Returns True when enabled."""
    global _enabled
    api_key = os.environ.get("DD_API_KEY", "").strip()
    if not api_key:
        logger.info("[TELEMETRY] Desactivada (sin DD_API_KEY)")
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
    env = os.environ.get("DD_ENV", "").strip() or DEFAULT_ENV
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
    _enabled = True
    logger.info(
        "[TELEMETRY] LLM Observability → %s (ml_app=%s env=%s version=%s)", site, ml_app, env, version or "?"
    )
    return True


def is_enabled() -> bool:
    return _enabled


@contextmanager
def _agent_span(session_id: str) -> Iterator[Any]:
    from ddtrace.llmobs import LLMObs

    span = LLMObs.agent(name="dexter", session_id=session_id)
    try:
        yield span
    except _CLIENT_GONE:
        annotate(span, tags={"client_disconnected": "true"})
        raise
    except BaseException as exc:
        span.set_exc_info(type(exc), exc, exc.__traceback__)
        raise
    finally:
        span.finish()


def agent_span(session_id: str) -> ContextManager[Any]:
    """Root span for one user turn; nullcontext (yields None) when disabled.

    Errors mark the span (status:error, what the error-rate monitor counts);
    a client disconnect does not.
    """
    if not _enabled:
        return nullcontext()
    return _agent_span(session_id)


def record_failed_request(session_id: str, exc: BaseException, *, input_data: Any = None) -> None:
    """Error agent span for a turn that failed before the agent ran (e.g. MCP server down).

    Without it those failures have no span and the error-rate monitor reads 0%.
    """
    if not _enabled:
        return
    try:
        with agent_span(session_id) as span:
            annotate(span, input_data=input_data, tags={"failure_stage": "setup"})
            span.set_exc_info(type(exc), exc, exc.__traceback__)
    except Exception as err:  # noqa: BLE001 — telemetry must never break a request
        logger.warning("[TELEMETRY] record_failed_request falló: %s", err)


def llm_span(model_id: str) -> ContextManager[Any]:
    """Span for one model call. ``model_id`` is LiteLLM's ``provider/model``."""
    if not _enabled:
        return nullcontext()
    from ddtrace.llmobs import LLMObs

    provider, _, model = model_id.partition("/")
    return LLMObs.llm(model_name=model, model_provider=provider, name="chat")


def start_tool_span(name: str) -> Any:
    """Start (and activate) a tool span; finish it with ``finish_tool_span``."""
    if not _enabled:
        return None
    from ddtrace.llmobs import LLMObs

    return LLMObs.tool(name=name)


def finish_tool_span(
    span: Any, *, input_data: Any, output_data: Any, error: bool = False, tags: dict[str, str] | None = None
) -> None:
    if span is None:
        return
    if tags:
        annotate(span, input_data=input_data, output_data=output_data, tags=tags)
    else:
        annotate(span, input_data=input_data, output_data=output_data)
    if error:
        span.error = 1
    span.finish()


def annotate(span: Any, **fields: Any) -> None:
    """LLMObs.annotate that never breaks a request because of telemetry."""
    if span is None:
        return
    from ddtrace.llmobs import LLMObs

    try:
        LLMObs.annotate(span=span, **fields)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TELEMETRY] annotate falló: %s", exc)


def export_ids(span: Any) -> dict[str, str] | None:
    """``{trace_id, span_id}`` of an LLMObs span, to attach evals and feedback; None when disabled."""
    if span is None or not _enabled:
        return None
    from ddtrace.llmobs import LLMObs

    try:
        exported = LLMObs.export_span(span=span)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TELEMETRY] export_span falló: %s", exc)
        return None
    if not exported:
        return None
    return {"trace_id": exported["trace_id"], "span_id": exported["span_id"]}


def submit_evaluations(ids: dict[str, str] | None, results: list[Any]) -> None:
    """Rule-check results (``backend.evals.checks.EvalResult``) as boolean evals on the agent span.

    Only enqueues: ddtrace's eval writer sends them in the background.
    """
    if not ids or not _enabled:
        return
    from ddtrace.llmobs import LLMObs

    from backend.evals.checks import CHECKS_VERSION

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
    """A user's 👍/👎 (``value`` "up"/"down") as LLMObs feedback on the agent span."""
    if not _enabled:
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
