"""
LLM configuration: Groq only, routed with strands' native ModelRouter.

Anonymous requests get the cheapest model; requests authenticated with API_KEY
get the larger model first and fall back to the small one. Failover is handled
by ``strands.models.ModelRouter`` (``FallbackStrategy``), so nothing in litellm
is patched.
"""

import importlib.metadata as _meta
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Guard against known-malicious LiteLLM versions (supply chain attack, March 2026).
# Versions 1.82.7 and 1.82.8 exfiltrate API keys; refuse to start.
_litellm_version = _meta.version("litellm")
if _litellm_version in ("1.82.7", "1.82.8"):
    sys.exit(
        f"SECURITY ERROR: litellm=={_litellm_version} contains a supply chain compromise "
        "that exfiltrates API keys. Downgrade to litellm==1.82.6 immediately and rotate "
        "any keys that may have been exposed."
    )

from strands.models import ModelRouter  # noqa: E402
from strands.models.litellm import LiteLLMModel  # noqa: E402

logger = logging.getLogger(__name__)

PROVIDER_LABEL = "Groq"
API_KEY_ENV = "GROQ_API_KEY"

# Only the cheapest model is exposed to anonymous users to avoid burning quota.
MODELS_PUBLIC = ["groq/openai/gpt-oss-20b"]
# Authenticated requests: preferred model first, then fallbacks in order.
MODELS_AUTHENTICATED = ["groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"]
# Both gpt-oss models have a 131k-token window on Groq; the harness context
# manager uses it to decide when to compress the conversation.
CONTEXT_WINDOW = 131_072

TraceSink = Callable[[dict[str, Any]], None]


def groq_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def model_ids(authenticated: bool) -> list[str]:
    return list(MODELS_AUTHENTICATED if authenticated else MODELS_PUBLIC)


def _preview_messages(messages: list[dict[str, Any]], limit: int = 800) -> list[dict[str, str]]:
    """Flatten Strands messages to ``{role, content}`` strings for the trace panel."""
    preview = []
    for msg in messages:
        parts = []
        for block in msg.get("content", []):
            if "text" in block:
                parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                parts.append(f"[toolUse {tu.get('name')}({tu.get('input')})]")
            elif "toolResult" in block:
                texts = " ".join(c.get("text", "") for c in block["toolResult"].get("content", []))
                parts.append(f"[toolResult {texts}]")
        content = "\n".join(parts)
        if len(content) > limit:
            content = content[:limit] + "…[truncated]"
        preview.append({"role": msg.get("role", "?"), "content": content})
    return preview


class TracedLiteLLMModel(LiteLLMModel):
    """LiteLLMModel that reports every model call attempt to a per-request trace sink.

    Emits ``llm_call`` before the request and ``model_attempt`` (success/failed)
    after it. Under a ModelRouter, a failover shows up as one failed attempt
    followed by another ``llm_call`` on the next candidate.
    """

    def __init__(self, *, trace: TraceSink | None = None, **config: Any) -> None:
        super().__init__(**config)
        self._trace = trace

    def _emit(self, event: dict[str, Any]) -> None:
        if self._trace is not None:
            self._trace(event)

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):  # type: ignore[override]
        model_id = self.get_config()["model_id"]
        self._emit({"type": "llm_call", "model": model_id, "messages": _preview_messages(messages)})
        logger.info("[LLM] Llamando a %s", model_id)
        try:
            async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield event
        except Exception as exc:
            logger.warning("[LLM] ✗ Falló %s → %s", model_id, str(exc)[:120])
            self._emit({"type": "model_attempt", "model": model_id, "status": "failed", "error": str(exc)[:120]})
            raise
        self._emit({"type": "model_attempt", "model": model_id, "status": "success"})


def build_model(authenticated: bool, trace: TraceSink | None = None) -> ModelRouter:
    """Build a ModelRouter over the Groq models allowed for this request.

    A fresh router and fresh model instances per request keep the trace sink
    request-scoped.

    Raises:
        RuntimeError: If GROQ_API_KEY is not set.
    """
    if not groq_configured():
        raise RuntimeError(f"No LLM provider configured. Set {API_KEY_ENV}.")
    return ModelRouter(
        [
            TracedLiteLLMModel(model_id=m, trace=trace, context_window_limit=CONTEXT_WINDOW)
            for m in model_ids(authenticated)
        ]
    )
