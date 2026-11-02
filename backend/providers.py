"""
LLM configuration: Groq only, routed with strands' native ModelRouter.

Anonymous requests get the cheapest model; requests authenticated with API_KEY
get the larger model first and fall back to the small one. Failover is handled
by ``strands.models.ModelRouter`` (``FallbackStrategy``), so nothing in litellm
is patched.
"""

import importlib.metadata as _meta
import json
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

from backend import telemetry  # noqa: E402

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

# Groq's gpt-oss accepts low | medium | high and defaults to medium. The model
# reasons before any visible output, so effort is pure latency: the call that
# opens a turn ("routing") only has to pick a tool, or answer an off-topic
# question directly, and gets low; the narration after tool results ("final")
# keeps medium. Both are overridable by env (rollback and the effort A/B).
REASONING_EFFORTS = ("low", "medium", "high")
DEFAULT_REASONING_EFFORT = {"routing": "low", "final": "medium"}


def _effort_from_env(name: str, default: str) -> str:
    """Read a reasoning effort from env; blank or invalid values fall back to ``default``."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value not in REASONING_EFFORTS:
        # Groq would reject the request with a 400 that nothing retries.
        logger.warning("[LLM] %s=%r no es válido (%s); uso %s", name, value, "|".join(REASONING_EFFORTS), default)
        return default
    return value


REASONING_EFFORT_ROUTING = _effort_from_env("REASONING_EFFORT_ROUTING", DEFAULT_REASONING_EFFORT["routing"])
REASONING_EFFORT_FINAL = _effort_from_env("REASONING_EFFORT_FINAL", DEFAULT_REASONING_EFFORT["final"])


def groq_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def model_ids(authenticated: bool) -> list[str]:
    return list(MODELS_AUTHENTICATED if authenticated else MODELS_PUBLIC)


# Per-message cap for the input messages sent to the tracing backends.
TRACE_MESSAGE_LIMIT = 16_000


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


def _tool_definitions(tool_specs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Strands tool specs → LLMObs tool definitions (``{name, description, schema}``)."""
    return [
        {
            "name": spec.get("name", ""),
            "description": spec.get("description", ""),
            "schema": spec.get("inputSchema", {}).get("json", {}),
        }
        for spec in tool_specs or []
    ]


def call_role(messages: list[dict[str, Any]]) -> str:
    """``"routing"`` when the call opens a turn (last message is the user's text), else ``"final"``.

    A call after tool results is ``"final"`` even if it calls another tool: the
    higher effort is the safe side. A routing call is also the final answer
    when no tool is needed (off-topic questions).
    """
    if not messages:
        return "routing"
    last = messages[-1]
    if last.get("role") != "user":
        return "final"
    return "final" if any("toolResult" in block for block in last.get("content", [])) else "routing"


class _StreamedOutput:
    """Collects the text, tool calls and token usage of one streamed model call."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._calls: list[dict[str, str]] = []
        self._usage: dict[str, int] = {}

    def add(self, event: dict[str, Any]) -> None:
        delta = event.get("contentBlockDelta", {}).get("delta", {})
        if text := delta.get("text"):
            self._parts.append(text)
        elif "toolUse" in delta and self._calls:
            self._calls[-1]["input"] += delta["toolUse"].get("input", "")
        elif tool_use := event.get("contentBlockStart", {}).get("start", {}).get("toolUse"):
            self._parts.append(f"\n[toolUse {tool_use.get('name')}]")
            self._calls.append({"name": tool_use.get("name", ""), "tool_id": tool_use.get("toolUseId", ""), "input": ""})
        elif usage := event.get("metadata", {}).get("usage"):
            self._usage = usage

    def text(self) -> str:
        return "".join(self._parts).strip()

    def tool_calls(self) -> list[dict[str, Any]]:
        """Tool calls in the LLMObs message shape; arguments arrive as streamed JSON text."""
        calls = []
        for call in self._calls:
            try:
                arguments = json.loads(call["input"]) if call["input"] else {}
            except ValueError:
                arguments = {"raw": call["input"]}
            calls.append({"name": call["name"], "arguments": arguments, "tool_id": call["tool_id"], "type": "function"})
        return calls

    def metrics(self) -> dict[str, int]:
        u = self._usage
        metrics = {
            "input_tokens": u.get("inputTokens", 0),
            "output_tokens": u.get("outputTokens", 0),
            "total_tokens": u.get("totalTokens", 0),
        }
        if "cacheReadInputTokens" in u:
            metrics["cache_read_input_tokens"] = u["cacheReadInputTokens"]
        return metrics if u else {}


class TracedLiteLLMModel(LiteLLMModel):
    """LiteLLMModel that reports every model call attempt to a per-request trace sink.

    Emits ``llm_call`` before the request and ``model_attempt`` (success/failed)
    after it. Under a ModelRouter, a failover shows up as one failed attempt
    followed by another ``llm_call`` on the next candidate.

    Also sets ``reasoning_effort`` per call from ``call_role(messages)``. It is a
    pure function of the messages, so retries and fallback candidates of the
    same call get the same effort.
    """

    def __init__(
        self,
        *,
        trace: TraceSink | None = None,
        reasoning_effort: dict[str, str] | None = None,
        **config: Any,
    ) -> None:
        super().__init__(**config)
        self._trace = trace
        self._effort = reasoning_effort or {"routing": REASONING_EFFORT_ROUTING, "final": REASONING_EFFORT_FINAL}

    def reasoning_effort_for(self, messages: list[dict[str, Any]]) -> str:
        # An explicit params={"reasoning_effort": ...} wins over the per-role effort.
        params = self.get_config().get("params") or {}
        return params.get("reasoning_effort") or self._effort[call_role(messages)]

    def format_request(self, messages, *args, **kwargs):  # type: ignore[override]
        request = super().format_request(messages, *args, **kwargs)
        # LiteLLM maps it to Groq's top-level `reasoning_effort` for gpt-oss.
        request["reasoning_effort"] = self.reasoning_effort_for(messages)
        return request

    def _emit(self, event: dict[str, Any]) -> None:
        if self._trace is not None:
            self._trace(event)

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):  # type: ignore[override]
        model_id = self.get_config()["model_id"]
        role, effort = call_role(messages), self.reasoning_effort_for(messages)
        self._emit(
            {
                "type": "llm_call",
                "model": model_id,
                "call_role": role,
                "reasoning_effort": effort,
                "messages": _preview_messages(messages),
            }
        )
        logger.info("[LLM] Llamando a %s (%s, reasoning_effort=%s)", model_id, role, effort)
        with telemetry.llm_span(model_id) as span:
            output = _StreamedOutput()
            try:
                async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                    output.add(event)
                    yield event
            except Exception as exc:
                logger.warning("[LLM] ✗ Falló %s → %s", model_id, str(exc)[:120])
                self._emit({"type": "model_attempt", "model": model_id, "status": "failed", "error": str(exc)[:120]})
                raise
            finally:
                system = [{"role": "system", "content": system_prompt}] if system_prompt else []
                reply: dict[str, Any] = {"role": "assistant", "content": output.text()}
                if calls := output.tool_calls():
                    reply["tool_calls"] = calls
                telemetry.annotate(
                    span,
                    input_data=system + _preview_messages(messages, limit=TRACE_MESSAGE_LIMIT),
                    output_data=[reply],
                    tool_definitions=_tool_definitions(tool_specs),
                    metrics=output.metrics(),
                    metadata={"call_role": role, "reasoning_effort": effort},
                    tags={"call_role": role, "reasoning_effort": effort},
                )
        self._emit({"type": "model_attempt", "model": model_id, "status": "success"})


class RetryMalformedToolCalls:
    """Retry a model call that Groq rejected because the model mangled a tool call.

    gpt-oss on Groq sometimes leaks its channel tokens into the tool name (e.g.
    ``compare_pokemon<|channel|>analysis``); Groq then fails the request with
    ``tool_use_failed``. A fresh sample almost always succeeds, so retry it a
    couple of times per invocation instead of failing the whole reply.
    """

    MAX_RETRIES = 2
    _STATE_KEY = "mcpokedex:tool_call_retries"

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import AfterModelCallEvent

        registry.add_callback(AfterModelCallEvent, self._on_model_result)

    @staticmethod
    def is_malformed_tool_call(exc: BaseException | None) -> bool:
        while exc is not None:
            text = str(exc)
            if "tool_use_failed" in text or "Tool call validation failed" in text:
                return True
            exc = exc.__cause__ or exc.__context__
        return False

    def _on_model_result(self, event) -> None:
        if event.retry or not self.is_malformed_tool_call(event.exception):
            return
        retries = event.invocation_state.get(self._STATE_KEY, 0)
        if retries >= self.MAX_RETRIES:
            return
        event.invocation_state[self._STATE_KEY] = retries + 1
        logger.warning("[LLM] Tool call malformado por el modelo; reintentando (%d/%d)", retries + 1, self.MAX_RETRIES)
        event.retry = True


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
