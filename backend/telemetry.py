"""
OpenTelemetry tracing for the agent, exported to Datadog LLM Observability.

Strands emits spans for every agent invocation, event-loop cycle, model call and
tool call. With ``DD_API_KEY`` set they are sent agentless (straight to Datadog's
OTLP intake, no Datadog Agent on the host), using the GenAI semantic conventions
Datadog expects. Without it, tracing stays off and nothing is exported.

Any ``OTEL_*`` variable already in the environment wins over the defaults below,
so the same code can point at another OTLP backend (Phoenix, Langfuse…) by
setting ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` and ``OTEL_EXPORTER_OTLP_TRACES_HEADERS``.

Must run before the first agent is built: Strands reads
``OTEL_SEMCONV_STABILITY_OPT_IN`` when it creates its tracer.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Becomes ml_app in Datadog (the root span's service name).
DEFAULT_ML_APP = "McpokedexAgent"


def setup_telemetry() -> bool:
    """Enable OTLP trace export if configured. Returns True when enabled."""
    api_key = os.environ.get("DD_API_KEY", "").strip()
    custom_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    if not api_key and not custom_endpoint:
        logger.info("[TELEMETRY] Desactivada (sin DD_API_KEY ni OTEL_EXPORTER_OTLP_TRACES_ENDPOINT)")
        return False

    site = os.environ.get("DD_SITE", "datadoghq.com").strip()
    defaults = {
        "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
        "OTEL_SERVICE_NAME": os.environ.get("DD_LLMOBS_ML_APP", DEFAULT_ML_APP),
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"https://otlp.{site}/v1/traces",
    }
    if api_key:
        defaults["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] = f"dd-api-key={api_key},dd-otlp-source=llmobs"
    for name, value in defaults.items():
        os.environ.setdefault(name, value)

    from strands.telemetry import StrandsTelemetry

    StrandsTelemetry().setup_otlp_exporter()
    logger.info(
        "[TELEMETRY] Trazas OTLP → %s (ml_app=%s)",
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"],
        os.environ["OTEL_SERVICE_NAME"],
    )
    return True
