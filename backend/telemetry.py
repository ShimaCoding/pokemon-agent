"""
Datadog LLM Observability (Agent Observability) for the agent.

With ``DD_API_KEY`` set, every request becomes one trace in Datadog:

    agent "dexter" (session = conversation_id)
    ├── llm   one per model call: messages, output, tokens (incl. cached), model
    └── tool  one per tool call: input and result

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

import logging
import os
from contextlib import nullcontext
from typing import Any, ContextManager

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


def agent_span(session_id: str) -> ContextManager[Any]:
    """Root span for one user turn; nullcontext (yields None) when disabled."""
    if not _enabled:
        return nullcontext()
    from ddtrace.llmobs import LLMObs

    return LLMObs.agent(name="dexter", session_id=session_id)


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
