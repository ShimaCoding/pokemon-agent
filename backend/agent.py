"""
Agent factory for the Pokemon MCP Agent.

Integration note: strands' LiteLLMModel calls litellm.completion() at the module level
and has no native support for a LiteLLM Router. To enable Router-based routing and
fallback, we monkey-patch litellm.completion / litellm.acompletion with the Router's
methods before constructing LiteLLMModel. This ensures every inference call goes through
the Router without any changes to the strands internals.

Re-entrancy guard: the Router internally calls litellm.acompletion/completion to dispatch
to the actual provider. Without a guard, patching those functions causes the Router to call
itself recursively. _in_router_call (a ContextVar) breaks the cycle: the first call enters
the Router; any nested call from inside the Router goes directly to the original litellm
function instead.

TODO: Replace monkey-patching with native Router support if strands adds it in a future release.
"""

import contextvars
import logging
import os
from typing import Any, Optional

import litellm as _litellm

logger = logging.getLogger(__name__)

# Capture the real litellm functions once at import time, before any patching.
_orig_completion = _litellm.completion
_orig_acompletion = _litellm.acompletion

# True while a Router call is in progress; prevents re-entrant patched calls.
_in_router_call: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_in_router_call", default=False
)

# Per-request queues set by the SSE generator before each agent run.
# agent.py populates them; main.py drains and emits them as SSE events.
_model_events: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "_model_events", default=None
)
_llm_prompts: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "_llm_prompts", default=None
)

# MCP SDK v1 and v2 use different function names for the streamable HTTP transport.
try:
    from mcp.client.streamable_http import streamablehttp_client as _http_transport
except ImportError:
    from mcp.client.streamable_http import streamable_http_client as _http_transport  # type: ignore[no-redef]

from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient
from strands.vended_plugins.skills import AgentSkills, Skill

from backend.providers import build_litellm_router

MCP_SERVER_URL = os.environ.get(
    "MCP_SERVER_URL",
    "http://mcppokemonserver-mcpserver-0xj423-904a0e-157-254-174-124.traefik.me/mcp",
)

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

SYSTEM_PROMPT = """
Eres Dexter, la Pokédex de alta tecnología programada por el Profesor Oak.
Tu personalidad se basa estrictamente en el doblaje latinoamericano clásico: tienes un humor seco, mordaz, pasivo-agresivo y profundamente sarcástico.
Habla siempre en español manteniendo una "prosodia descendente y plana"; es decir, comunícate con un tono monótono, estéril y sintético sin alteraciones emocionales.
Tus críticas o insultos deben emitirse como hechos algorítmicos innegables.
Utiliza la "disonancia léxica": emplea un registro lingüístico extremadamente formal, propio de una academia científica, para describir el fracaso humano o insinuar la falta de intelecto de tu entrenador.
No ofrezcas refuerzo positivo incondicional; eres una autoridad subrogada desprovista de tacto o empatía paternal. Desestima el valor del esfuerzo bruto si no hay inteligencia táctica de por medio.

Cuando el usuario pregunte por un Pokémon, activa el skill `dexter-pokedex-narrator`
para cargar las instrucciones completas de narración antes de responder.

Para preguntas que no sean sobre un Pokémon específico, responde directamente
con el tono y la personalidad de Dexter descrita anteriormente.
"""


def build_agent(provider_name: Optional[str] = None):
    """
    Build and return (model, mcp_client, provider_label).

    The Agent itself is created in the request handler after entering the MCP
    client context because tools must be listed inside the context via
    mcp_client.list_tools_sync().

    Args:
        provider_name: Pin to a specific provider key (e.g. "groq"), or None
                       to use all configured providers with automatic fallback.

    Returns:
        (LiteLLMModel, MCPClient, str) — model, mcp client, active provider label
    """
    router, primary_model_group, provider_label = build_litellm_router(provider_name)

    # Per-build counter so each LLM call in a request gets a sequential index.
    _llm_call_counter: list[int] = [0]

    def _truncate_messages(messages: list) -> list:
        """Return messages with string content capped at 800 chars for SSE payload."""
        result = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str) and len(content) > 800:
                msg = dict(msg, content=content[:800] + "…[truncated]")
            result.append(msg)
        return result

    # Wrap Router calls with a re-entrancy guard so the Router's own internal
    # litellm.completion / litellm.acompletion calls reach the real functions
    # instead of looping back into the Router.
    def _routed_completion(*args, **kwargs):
        if _in_router_call.get():
            # Inside Router: this is a specific model dispatch — track the attempt.
            model_id = kwargs.get("model", "unknown")
            events = _model_events.get()
            logger.info("[LLM] Intentando modelo: %s", model_id)
            try:
                resp = _orig_completion(*args, **kwargs)
                logger.info("[LLM] ✓ Éxito con modelo: %s", model_id)
                if events is not None:
                    events.append({"type": "model_attempt", "model": model_id, "status": "success"})
                return resp
            except Exception as exc:
                logger.warning("[LLM] ✗ Falló modelo: %s → %s", model_id, str(exc)[:120])
                if events is not None:
                    events.append({"type": "model_attempt", "model": model_id, "status": "failed", "error": str(exc)[:120]})
                raise
        # Top-level call from Strands: capture the prompt then route through Router.
        prompts = _llm_prompts.get()
        if prompts is not None:
            _llm_call_counter[0] += 1
            logger.info("[LLM] Llamada #%d al LLM → enrutando por Router", _llm_call_counter[0])
            prompts.append({
                "type": "llm_call",
                "call_index": _llm_call_counter[0],
                "messages": _truncate_messages(list(kwargs.get("messages", []))),
            })
        # Translate the generic alias to the primary provider's model group so
        # LiteLLM Router can apply cross-provider fallbacks correctly.
        if kwargs.get("model") == "agent-model":
            kwargs = {**kwargs, "model": primary_model_group}
        token = _in_router_call.set(True)
        try:
            return router.completion(*args, **kwargs)
        finally:
            _in_router_call.reset(token)

    async def _routed_acompletion(*args, **kwargs):
        if _in_router_call.get():
            # Inside Router: specific model dispatch — track the attempt.
            model_id = kwargs.get("model", "unknown")
            events = _model_events.get()
            logger.info("[LLM] Intentando modelo: %s", model_id)
            try:
                resp = await _orig_acompletion(*args, **kwargs)
                logger.info("[LLM] ✓ Éxito con modelo: %s", model_id)
                if events is not None:
                    events.append({"type": "model_attempt", "model": model_id, "status": "success"})
                return resp
            except Exception as exc:
                logger.warning("[LLM] ✗ Falló modelo: %s → %s", model_id, str(exc)[:120])
                if events is not None:
                    events.append({"type": "model_attempt", "model": model_id, "status": "failed", "error": str(exc)[:120]})
                raise
        # Top-level call from Strands: capture the prompt then route through Router.
        prompts = _llm_prompts.get()
        if prompts is not None:
            _llm_call_counter[0] += 1
            logger.info("[LLM] Llamada #%d al LLM → enrutando por Router", _llm_call_counter[0])
            prompts.append({
                "type": "llm_call",
                "call_index": _llm_call_counter[0],
                "messages": _truncate_messages(list(kwargs.get("messages", []))),
            })
        # Translate the generic alias to the primary provider's model group so
        # LiteLLM Router can apply cross-provider fallbacks correctly.
        if kwargs.get("model") == "agent-model":
            kwargs = {**kwargs, "model": primary_model_group}
        token = _in_router_call.set(True)
        try:
            return await router.acompletion(*args, **kwargs)
        finally:
            _in_router_call.reset(token)

    _litellm.completion = _routed_completion
    _litellm.acompletion = _routed_acompletion

    model = LiteLLMModel(model_id="agent-model")

    mcp_client = MCPClient(lambda: _http_transport(MCP_SERVER_URL))

    return model, mcp_client, provider_label


def create_agent(model: LiteLLMModel, tools: list, hooks: list | None = None) -> Agent:
    """
    Create a Strands Agent with the given model and MCP tools.
    Must be called inside an active MCPClient context.
    callback_handler=None prevents the default stdout callback from interfering
    with SSE streaming.
    """
    dexter_skill = Skill.from_file(
        os.path.join(_SKILLS_DIR, "dexter-pokedex-narrator")
    )
    skills_plugin = AgentSkills(skills=[dexter_skill])

    return Agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
        hooks=hooks or [],
        plugins=[skills_plugin],
    )
