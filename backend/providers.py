"""
LLM configuration: Groq only, routed with strands' native ModelRouter.

Anonymous requests get the cheapest model; requests authenticated with API_KEY
get the larger model first and fall back to the small one. Failover is handled
by ``strands.models.ModelRouter`` with ``routing.CooldownFallbackStrategy``
(ordered failover that also skips a model for a while after it is rate limited),
so nothing in litellm is patched.

With ``MODEL_CLASSIFIER_ENABLED`` on, ``routing.ClassifiedCooldownStrategy`` lets a
small classifier call (``ClassifierLiteLLMModel`` on the 20b) pick the model that
opens each turn (#61).
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

import litellm  # noqa: E402
from strands.models import ModelRouter  # noqa: E402
from strands.models.routing import RoutingCandidate  # noqa: E402
from strands.models.litellm import LiteLLMModel  # noqa: E402

from backend import telemetry  # noqa: E402
from backend.routing import CANDIDATE_DESCRIPTIONS, ClassifiedCooldownStrategy, CooldownFallbackStrategy  # noqa: E402

logger = logging.getLogger(__name__)

PROVIDER_LABEL = "Groq"
API_KEY_ENV = "GROQ_API_KEY"

# Preferred model first, then fallbacks in order. Anonymous requests get the 120b
# too since #42: with the 20b the narration stayed under 76 % grounded (judge).
MODELS_AUTHENTICATED = ["groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"]
MODELS_PUBLIC = list(MODELS_AUTHENTICATED)
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


# Classifier routing (#61): off by default; the evals decide whether it ships.
CLASSIFIER_MODEL = "groq/openai/gpt-oss-20b"
DEFAULT_CLASSIFIER_TIMEOUT_SECONDS = 2.0


def classifier_enabled() -> bool:
    """``MODEL_CLASSIFIER_ENABLED`` (1/true/on/yes), read per request so it can be flipped without a rebuild."""
    return os.environ.get("MODEL_CLASSIFIER_ENABLED", "").strip().lower() in ("1", "true", "on", "yes")


def classifier_timeout() -> float:
    raw = os.environ.get("MODEL_CLASSIFIER_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_CLASSIFIER_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_CLASSIFIER_TIMEOUT_SECONDS
    return value if 0 < value < 60 else DEFAULT_CLASSIFIER_TIMEOUT_SECONDS


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


class ClassifierLiteLLMModel(LiteLLMModel):
    """The routing classifier's model: one non-streamed structured-output call, traced.

    ``LiteLLMModel.structured_output`` sends neither ``params`` (so no ``reasoning_effort``)
    nor usage back, so this overrides it: ``response_format`` = the classifier's schema,
    ``reasoning_effort=low``, a small ``max_tokens``, an ``llm`` span tagged
    ``call_role: classifier``, and a ``stop`` event with the usage. ``last`` keeps the
    reasoning, tokens and error of the latest call for the ``model_route`` event.
    """

    MAX_TOKENS = 512
    REASONING_CHARS = 300

    def __init__(self, *, request_timeout: float | None = None, **config: Any) -> None:
        super().__init__(**config)
        self._request_timeout = request_timeout
        self.last: dict[str, Any] = {}

    @staticmethod
    def _flatten(prompt: list[dict[str, Any]], system_prompt: str | None) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        for msg in prompt:
            text = "\n".join(block["text"] for block in msg.get("content", []) if "text" in block)
            messages.append({"role": msg.get("role", "user"), "content": text})
        return messages

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # type: ignore[override]
        model_id = self.get_config()["model_id"]
        messages = self._flatten(prompt, system_prompt)
        self.last = {}
        request: dict[str, Any] = {
            **self.client_args,
            "model": model_id,
            "messages": messages,
            "response_format": output_model,
            "reasoning_effort": "low",
            "max_tokens": self.MAX_TOKENS,
            "stream": False,
        }
        if self._request_timeout:
            request["timeout"] = self._request_timeout
        with telemetry.llm_span(model_id) as span:
            content, reasoning, metrics = "", "", {}
            try:
                response = await litellm.acompletion(**request)
                message = response.choices[0].message
                content = message.content or ""
                reasoning = (getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None) or "").strip()
                if usage := getattr(response, "usage", None):
                    metrics = {
                        "input_tokens": usage.prompt_tokens or 0,
                        "output_tokens": usage.completion_tokens or 0,
                        "total_tokens": usage.total_tokens or 0,
                    }
                self.last = {"reasoning": reasoning[: self.REASONING_CHARS], **metrics}
                result = output_model.model_validate_json(content)
            except BaseException as exc:  # CancelledError included: the strategy's timeout cancels us
                self.last.setdefault("error", type(exc).__name__)
                raise
            finally:
                telemetry.annotate(
                    span,
                    input_data=messages[-1:] if messages else [],
                    output_data=[{"role": "assistant", "content": content}],
                    metrics=metrics,
                    metadata={"call_role": "classifier", "reasoning_effort": "low", "reasoning": reasoning[:500]},
                    tags={"call_role": "classifier", "reasoning_effort": "low"},
                )
        usage_event = {
            "inputTokens": metrics.get("input_tokens", 0),
            "outputTokens": metrics.get("output_tokens", 0),
            "totalTokens": metrics.get("total_tokens", 0),
        }
        yield {"output": result}
        # Same shape as the SDK's ModelStopReason: ClassifierStrategy adds the usage to the request.
        yield {"stop": ("end_turn", {"role": "assistant", "content": [{"text": content}]}, usage_event, {"latencyMs": 0})}


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
    request-scoped; model health across requests lives in CooldownFallbackStrategy.

    With ``MODEL_CLASSIFIER_ENABLED`` the candidates carry a description (big/small)
    and ``ClassifiedCooldownStrategy`` picks the opening model; ``trace`` also gets
    its ``model_route`` event. Off, it is plain ``CooldownFallbackStrategy``.

    Raises:
        RuntimeError: If GROQ_API_KEY is not set.
    """
    if not groq_configured():
        raise RuntimeError(f"No LLM provider configured. Set {API_KEY_ENV}.")
    ids = model_ids(authenticated)
    classify = classifier_enabled() and len(ids) > 1

    def description(index: int) -> str | None:
        if not classify:
            return None
        return CANDIDATE_DESCRIPTIONS["big" if index == 0 else "small"]

    # Candidate name = model id: CooldownFallbackStrategy keys its process-wide table on it.
    candidates = [
        RoutingCandidate(
            model=TracedLiteLLMModel(model_id=m, trace=trace, context_window_limit=CONTEXT_WINDOW),
            name=m,
            description=description(i),
        )
        for i, m in enumerate(ids)
    ]
    if not classify:
        return ModelRouter(candidates, strategy=CooldownFallbackStrategy())
    timeout = classifier_timeout()
    # Its own instance (not the 20b candidate's): different call shape and state.
    classifier = ClassifierLiteLLMModel(model_id=CLASSIFIER_MODEL, request_timeout=timeout)
    return ModelRouter(candidates, strategy=ClassifiedCooldownStrategy(classifier, timeout=timeout, on_route=trace))
