"""
FastAPI application for MCPokédex.

Routes:
  GET  /health               — health check (+ deployed version)
  GET  /config               — public frontend config (e.g. require_api_key flag)
  POST /api/agent/run        — SSE stream: agent response, traces and widgets
  GET  /api/ui-resource      — widget HTML (`ui://` MCP Apps resource) + its CSP
  POST /api/mcp/call-tool    — widget → MCP server tool calls (UI-bound tools only)
  POST /api/feedback         — 👍/👎 on a reply → feedback in Datadog LLM Observability / Phoenix
  GET  /api/prompts|tools|resources — MCP server capabilities
  GET  /                     — serves the frontend (StaticFiles mount)

Security:
  - API key auth: set API_KEY env var; clients send the X-API-Key header.
    Authenticated requests skip the agent rate limit and get the larger model.
  - Admin (/admin/api/*): same header, checked against ADMIN_API_KEY; without it,
    against API_KEY. With neither set the admin API rejects everything.
  - Rate limiting: /api/agent/run is capped at 3 req/min per IP via slowapi.
    Local eval runs can lift that cap with RATE_LIMIT_DISABLED=1, which is
    ignored (with a warning) when DD_ENV is prod or preview.
  - Feedback: /api/feedback takes 10 req/min per IP, one vote per reply, and
    only for replies this process issued (in-memory registry, single worker).
  - CORS: controlled by ALLOWED_ORIGINS env var (comma-separated).
    Defaults to "*" when unset (local dev only — set a real domain in prod).
"""

import asyncio
import hmac
import itertools
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from typing import Any, Iterator, Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend import box, build_info, guards, mcp_gateway, telemetry, tool_cache, traces
from backend.database import get_queries, get_stats, init_db, log_query
from backend.evals import checks
from backend.feedback import registry as feedback_registry
from backend.providers import API_KEY_ENV, PROVIDER_LABEL, groq_configured
from backend.sessions import CONVERSATION_ID_RE, TRAINER_ID_RE, conversation_lock, prune_periodically
from backend.wiki_content import EVENTS_CATALOG, lessons_as_dicts

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Datadog LLM Observability and/or Phoenix (no-op without DD_API_KEY / PHOENIX_COLLECTOR_ENDPOINT).
telemetry.setup_telemetry()

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)

# ContextVar so exempt_when (called with zero args by slowapi) can access the
# current request's API key, which is set by the middleware below.
_req_api_key: ContextVar[str] = ContextVar("req_api_key", default="")

# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _key_matches(provided: str | None, env_name: str) -> bool:
    """True when ``provided`` is the key configured in ``env_name``; an unset key matches nothing."""
    expected = os.environ.get(env_name, "").strip()
    if not expected or not provided:
        return False
    # As bytes: compare_digest refuses non-ASCII str, and a header can carry anything.
    return hmac.compare_digest(provided.encode(), expected.encode())


def _is_valid_api_key(api_key: str | None) -> bool:
    """Return True if the provided key matches the configured API_KEY."""
    return _key_matches(api_key, "API_KEY")


def _is_valid_admin_key(api_key: str | None) -> bool:
    """The key that opens /admin/api/*: ADMIN_API_KEY, or API_KEY when the admin has no key of its own.

    They are separate because API_KEY changes the public chat (/config tells every visitor
    a key exists, and the settings dialog opens), and whoever holds it should not read
    what other visitors asked. With ADMIN_API_KEY set, API_KEY no longer opens the admin.
    """
    if os.environ.get("ADMIN_API_KEY", "").strip():
        return _key_matches(api_key, "ADMIN_API_KEY")
    return _key_matches(api_key, "API_KEY")


def _is_authenticated_request() -> bool:
    return _is_valid_api_key(_req_api_key.get())


# Envs where RATE_LIMIT_DISABLED never takes effect.
_PROTECTED_ENVS = {"prod", "preview"}


def rate_limit_disabled() -> bool:
    """True when RATE_LIMIT_DISABLED is set and DD_ENV is not prod/preview (local evals only)."""
    if os.environ.get("RATE_LIMIT_DISABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return False
    return os.environ.get("DD_ENV", "").strip().lower() not in _PROTECTED_ENVS


def _agent_run_exempt() -> bool:
    return rate_limit_disabled() or _is_authenticated_request()


async def require_api_key(api_key: str = Security(_api_key_header)) -> None:
    """Dependency: require a valid API key (used for admin endpoints)."""
    if not _is_valid_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not groq_configured():
        raise RuntimeError(f"No LLM provider configured. Set {API_KEY_ENV}.")
    logger.info("LLM provider: %s", PROVIDER_LABEL)
    if _cors_origins == ["*"]:
        logger.warning(
            "CORS configured with wildcard '*'. "
            "Set ALLOWED_ORIGINS in .env for production."
        )
    if os.environ.get("RATE_LIMIT_DISABLED", "").strip():
        if rate_limit_disabled():
            logger.warning("RATE_LIMIT_DISABLED: /api/agent/run has no rate limit (local evals only).")
        else:
            logger.warning("RATE_LIMIT_DISABLED ignored: DD_ENV=%s keeps the rate limit.", os.environ.get("DD_ENV"))
    init_db()
    # Hourly cleanups: expired conversations and boxes nobody touched in BOX_TTL_DAYS.
    cleanup_tasks = [asyncio.create_task(prune_periodically()), asyncio.create_task(box.purge_periodically())]
    try:
        yield
    finally:
        for task in cleanup_tasks:
            task.cancel()
        for task in cleanup_tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="MCPokédex", lifespan=lifespan)

app.state.limiter = limiter


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas consultas. Espera un momento antes de volver a intentarlo."},
        headers={"Retry-After": "60"},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# CORS: read from ALLOWED_ORIGINS (comma-separated). Defaults to "*" for local dev.
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "*")
_cors_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Add standard security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.middleware("http")
async def _capture_api_key(request: Request, call_next):
    """Store the request's X-API-Key in a ContextVar for use by exempt_when."""
    token = _req_api_key.set(request.headers.get("X-API-Key", ""))
    try:
        return await call_next(request)
    finally:
        _req_api_key.reset(token)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    # Generated by the browser (localStorage); keys the harness session snapshot.
    conversation_id: str = Field(..., pattern=CONVERSATION_ID_RE.pattern)
    # Also from the browser (localStorage), kept across conversations: whose box Bill opens.
    # Optional: bundles cached before it existed do not send it, and then the box tools
    # answer that the box is unavailable. It never leaves the server again: not in SSE
    # events, logs, spans or the query log.
    trainer_id: Optional[str] = Field(None, pattern=TRAINER_ID_RE.pattern)


class CallToolRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class FeedbackRequest(BaseModel):
    # Agent span ids from the reply's `done` event (Datadog's, or Phoenix's when only it is on):
    # 128-bit hex trace, decimal 64-bit span.
    trace_id: str = Field(..., pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(..., pattern=r"^[0-9]{1,20}$")
    value: str = Field(..., pattern=r"^(up|down)$")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/config")
async def config():
    """Public endpoint: frontend config flags."""
    return {"require_api_key": bool(os.environ.get("API_KEY", "").strip())}


@app.get("/health")
async def health():
    return {"status": "ok", "provider": PROVIDER_LABEL, "version": build_info.version()}


@app.get("/api/prompts")
@limiter.limit("10/minute")
async def get_prompts(request: Request):
    """Fetch the list of prompts from the MCP server."""
    try:
        return await asyncio.to_thread(mcp_gateway.list_prompts)
    except Exception as exc:
        logger.error("Error fetching prompts: %s", exc)
        return []


@app.get("/api/tools")
@limiter.limit("10/minute")
async def get_tools(request: Request):
    """Fetch the list of tools from the MCP server."""
    try:
        return await asyncio.to_thread(mcp_gateway.list_tools)
    except Exception as exc:
        logger.error("Error fetching tools: %s", exc)
        return []


@app.get("/api/resources")
@limiter.limit("10/minute")
async def get_resources(request: Request):
    """Fetch the list of resources from the MCP server."""
    try:
        return await asyncio.to_thread(mcp_gateway.list_resources)
    except Exception as exc:
        logger.error("Error fetching resources: %s", exc)
        return []


# ---------------------------------------------------------------------------
# MCP Apps — widget resources and widget → server tool calls
# ---------------------------------------------------------------------------


@app.get("/api/ui-resource")
@limiter.limit("30/minute")
async def get_ui_resource(request: Request, uri: str = Query(..., max_length=200)):
    """Return a widget's HTML and `_meta.ui` (csp, permissions) for the iframe host."""
    if not uri.startswith("ui://"):
        raise HTTPException(status_code=400, detail="Solo se aceptan URIs ui://")
    try:
        resource = await asyncio.to_thread(mcp_gateway.read_ui_resource, uri)
    except Exception as exc:
        logger.error("Error reading UI resource %s: %s", uri, exc)
        raise HTTPException(status_code=502, detail="No se pudo leer el recurso del servidor MCP")
    if resource is None:
        raise HTTPException(status_code=404, detail="Recurso UI desconocido")
    return JSONResponse(content=resource, headers={"Cache-Control": "public, max-age=300"})


@app.post("/api/mcp/call-tool")
@limiter.limit("20/minute", exempt_when=_is_authenticated_request)
async def call_tool(request: Request, body: CallToolRequest):
    """Forward a widget's `callServerTool` to the MCP server (UI-bound tools only)."""
    try:
        result = await asyncio.to_thread(mcp_gateway.call_ui_tool, body.name, body.arguments)
    except Exception as exc:
        logger.error("Error calling tool %s for widget: %s", body.name, exc)
        raise HTTPException(status_code=502, detail="Error al llamar al servidor MCP")
    if result is None:
        raise HTTPException(status_code=403, detail="Tool no disponible para widgets")
    return result


# ---------------------------------------------------------------------------
# Wiki content — lessons shown in the "ver cómo lo pensó" trace panel
# ---------------------------------------------------------------------------

_WIKI_CACHE_HEADERS = {"Cache-Control": "public, max-age=3600"}


@app.get("/api/wiki/lessons")
@limiter.limit("10/minute")
async def get_wiki_lessons(request: Request):
    """Return the full list of wiki lessons."""
    return JSONResponse(content=lessons_as_dicts(), headers=_WIKI_CACHE_HEADERS)


@app.get("/api/wiki/events-catalog")
@limiter.limit("10/minute")
async def get_wiki_events_catalog(request: Request):
    """Return the SSE events catalog (tooltips + related lesson per type)."""
    return JSONResponse(content=EVENTS_CATALOG, headers=_WIKI_CACHE_HEADERS)


# ---------------------------------------------------------------------------
# Feedback — 👍/👎 on a reply, tied to its Datadog trace
# ---------------------------------------------------------------------------


@app.post("/api/feedback", status_code=204)
@limiter.limit("10/minute")
async def post_feedback(request: Request, body: FeedbackRequest):
    """Forward a vote to the tracing backends; only for replies this process issued, once each."""
    status, conversation_id = feedback_registry.claim(body.span_id, body.trace_id)
    if status == "unknown":
        raise HTTPException(status_code=404, detail="Respuesta desconocida o expirada")
    if status == "voted":
        raise HTTPException(status_code=409, detail="Esta respuesta ya fue valorada")
    # The submitter is the server-side conversation id (the LLM Obs session), never client input.
    telemetry.submit_feedback(
        {"span_id": body.span_id, "trace_id": body.trace_id},
        value=body.value,
        submitter_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# Agent run (SSE)
# ---------------------------------------------------------------------------


@app.post("/api/agent/run")
@limiter.limit("3/minute", exempt_when=_agent_run_exempt)
async def run_agent(request: Request, body: RunRequest, api_key: str = Security(_api_key_header)):
    authenticated = _is_valid_api_key(api_key)
    # Every yielded SSE frame must reach the client as its own chunk (test_streaming.py).
    return StreamingResponse(
        _sse_generator(body.query, body.conversation_id, authenticated=authenticated, trainer_id=body.trainer_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )


def _sse(payload: dict) -> str:
    """Format a dict as an SSE data frame."""
    return f"data: {json.dumps(payload)}\n\n"


def _extract_result_text(tool_result: dict) -> str:
    """Extract plain text from a Strands ToolResult dict."""
    content = tool_result.get("content", [])
    if isinstance(content, list):
        return " ".join(c.get("text", str(c)) for c in content)
    return str(content)


class _ToolTrace:
    """
    Strands HookProvider that turns tool calls into SSE events.

    BeforeToolCallEvent / AfterToolCallEvent fire with the fully-parsed ToolUse
    (streamed tool input is still a partial string), so they're the reliable
    place to capture calls and results. Events go to ``emit`` in the order the
    hooks fire; the SSE generator drains them between stream events.

    After a UI-bound MCP tool (``_meta.ui.resourceUri``) runs, a ``ui_resource``
    event carries everything the frontend needs to mount its widget.

    A sub-agent's own tools get their trace from ``for_sub_agent``: its events carry
    ``agent`` (absent = the root agent), share the root's index sequence so no two
    calls of a run collide, and never include ``ui_resource``: a sub-agent does not
    talk in the chat, so an MCP tool it calls must not mount a widget there.
    ``names``, ``observations`` and ``count`` are this trace's calls only.
    """

    def __init__(self, emit, *, agent: str | None = None, widgets: bool = True, indexes: Iterator[int] | None = None) -> None:
        self._emit = emit
        self._agent = agent
        self._widgets = widgets
        self._indexes = indexes if indexes is not None else itertools.count()
        self._index_by_id: dict[str, int] = {}
        self._spans: dict[str, Any] = {}
        self.names: list[str] = []
        # Raw results (not the summarized display) for the quality checks.
        self.observations: list[checks.ToolObservation] = []

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._on_call)
        registry.add_callback(AfterToolCallEvent, self._on_result)

    def for_sub_agent(self, name: str) -> "_ToolTrace":
        """The trace for the tools of the sub-agent ``name`` (a hook on that agent)."""
        return _ToolTrace(self._emit, agent=name, widgets=False, indexes=self._indexes)

    def _send(self, event: dict) -> None:
        self._emit({**event, "agent": self._agent} if self._agent else event)

    def _on_call(self, event) -> None:
        uid = event.tool_use.get("toolUseId", "")
        name = event.tool_use.get("name", "unknown")
        index = next(self._indexes)
        self._index_by_id[uid] = index
        # An agent used as a tool (Agent.as_tool()) gets no tool span: its own agent span
        # (telemetry.SubAgentTrace) has the input, the output and the latency, and hangs
        # straight from this agent.
        if getattr(getattr(event, "selected_tool", None), "tool_type", None) != "agent":
            self._spans[uid] = telemetry.start_tool_span(name)
        self.names.append(name)
        args = dict(event.tool_use.get("input") or {})
        logger.info("[TOOL] Llamando tool #%d: %s | args: %s", index, name, str(args)[:120])
        self._send({"type": "tool_call", "index": index, "tool_id": uid, "tool": name, "args": args})

    def _on_result(self, event) -> None:
        uid = event.tool_use.get("toolUseId", "")
        name = event.tool_use.get("name", "unknown")
        index = self._index_by_id.get(uid, -1)
        text = _extract_result_text(event.result)
        logger.info("[TOOL] Resultado de %s: %.120s%s", name, text, "…" if len(text) > 120 else "")
        display = text
        # Set by the tool cache middleware (backend/tool_cache.py) for cacheable tools.
        cache = tool_cache.status(getattr(event, "invocation_state", None), uid)
        duration = getattr(event, "duration", None)
        duration_ms = round(duration * 1000, 2) if duration is not None else None
        error = event.result.get("status") == "error"
        self._send({"type": "tool_result", "index": index, "tool_id": uid, "tool": name, "result": display,
                    "status": "error" if error else "success",
                    "cached": cache in ("hit", "stale"), "duration_ms": duration_ms,
                    "cancelled": getattr(event, "cancel_message", None) is not None})
        telemetry.finish_tool_span(
            self._spans.pop(uid, None),
            input_data=dict(event.tool_use.get("input") or {}),
            output_data=display,
            error=error,
            tags={"cache": cache} if cache else None,
        )
        self.observations.append(
            checks.ToolObservation(
                name=name,
                args=dict(event.tool_use.get("input") or {}),
                text=text,
                error=error,
            )
        )

        if not self._widgets:
            return
        uri = mcp_gateway.ui_resource_uri(getattr(event.selected_tool, "mcp_tool", None))
        if uri and not error:
            self._send(
                {
                    "type": "ui_resource",
                    "index": index,
                    "tool_id": uid,
                    "tool": name,
                    "resource_uri": uri,
                    "tool_input": dict(event.tool_use.get("input") or {}),
                    "tool_result": mcp_gateway.to_call_tool_result(event.result),
                }
            )

    @property
    def count(self) -> int:
        return len(self.names)


def _submit_rule_evals(ids: dict[str, str], query: str, reply: str, tool_trace: _ToolTrace, messages: list) -> None:
    """Rule-based quality checks on the finished turn → Datadog evals. Pure CPU, never raises."""
    try:
        results = checks.evaluate_turn(
            checks.Turn(
                query=query,
                reply=reply,
                tools=tool_trace.observations,
                evidence=checks.evidence_from_messages(messages),
            )
        )
        for r in results:
            if not r.passed:
                logger.info("[EVAL] %s=fail %s", r.label, r.reasoning)
        telemetry.submit_evaluations(ids, results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EVAL] Las evaluaciones fallaron: %s", exc)


def _run_usage(result: Any) -> dict:
    """Token usage of the whole run, sub-agents included.

    ``result.metrics.accumulated_usage`` only counts the root agent's model calls; the
    request's ``invocation_context`` also adds up the agents it called as tools. Results
    without one (older Strands, test fakes) fall back to the metrics.
    """
    usage = getattr(getattr(result, "invocation_context", None), "usage", None)
    if usage:
        return dict(usage)
    return dict(result.metrics.accumulated_usage or {})


# "Usa el prompt <name> [con <arg>=<val>...]" — sent by the slash menu.
_PROMPT_RE = re.compile(
    r"(?:usa el prompt|ejecuta el prompt|aplicar el prompt)\s+([a-zA-Z0-9_./-]+)(?:\s+con\s+(.+))?",
    re.I,
)


async def _expand_prompt(query: str) -> str:
    """Replace a slash-menu prompt invocation with the MCP prompt's text."""
    m = _PROMPT_RE.match(query)
    if not m:
        return query
    prompt_name, args_str = m.group(1), m.group(2) or ""
    # Accept both comma-separated and space-separated key=value pairs
    args = {
        k.strip(): v.strip()
        for pair in re.split(r"[,\s]+", args_str)
        if "=" in pair
        for k, v in [pair.split("=", 1)]
    }
    try:
        prompt_text = (await asyncio.to_thread(mcp_gateway.get_prompt_text, prompt_name, args))[:2000]
    except Exception as exc:
        logger.warning("Could not fetch prompt %s: %s", prompt_name, exc)
        return query
    return (
        f"Using MCP prompt '{prompt_name}':\n\n{prompt_text}\n\n"
        "Please follow these instructions and provide the complete answer using the available tools."
    )


async def _sse_generator(
    query: str, conversation_id: str, authenticated: bool = False, trainer_id: str | None = None
):
    """
    Async generator that yields SSE events for one agent run.

    ``trainer_id`` only goes to ``create_agent`` (Bill's box tools, by closure): no event,
    log line, span or query-log row carries it.

    Event sequence:
      {"type": "start",         "provider": str, "conversation_id": str}
      {"type": "agent_init",    "mcp_tools_count": int, "tool_names": list, "skill_loaded": str, ...}
                                # tool_names also lists the agents used as tools (bill); mcp_tools_count does not
                                # skill_loaded: the narration protocol, always inline in SYSTEM_PROMPT
                                # turn: 1-based turn of the conversation; history_messages: after the last compaction
      {"type": "llm_call",      "call_index": int, "model": str, "messages": list, "timestamp_ms": int,
                                "call_role": "routing"|"final", "reasoning_effort": str, "agent": str?}
                                # routing: opens the turn (picks a tool or answers directly); final: after tool results
                                # call_index counts every model call of the run, sub-agents included
      {"type": "model_attempt", "model": str, "status": "success"|"failed", "error": str?, "timestamp_ms": int,
                                "agent": str?}
      {"type": "tool_call",     "index": int, "tool_id": str, "tool": str, "args": dict, "timestamp_ms": int,
                                "agent": str?}
      {"type": "tool_result",   "index": int, "tool_id": str, "tool": str, "result": str, "timestamp_ms": int,
                                "status": "success"|"error", "cached": bool, "duration_ms": float | None,
                                "cancelled": bool, "agent": str?}
                                # cancelled: never ran (agent.GroundedPokemonNames); result is the reason
      {"type": "ui_resource",   "index": int, "tool_id": str, "tool": str, "resource_uri": str,
                                "tool_input": dict, "tool_result": CallToolResult, "timestamp_ms": int}
                                # root agent only: a sub-agent's tools never mount a widget
      {"type": "text",          "delta": str}  # filtered by guards.StreamGuard: the first ~200 chars are held back
      {"type": "done",          "total_tool_calls": int, "elapsed_ms": int, "models_tried": list[str], tokens…,
                                # total_tool_calls and models_tried: the root agent's only;
                                # tokens and llm_calls: the whole run, sub-agents included
                                "cached_tokens": int, "llm_calls": int, "ttft_ms": int | None,
                                "guard": str | None,  # guards.REPLACEMENTS key when a guard replaced or cut the reply
                                # (a garbled answer that a retry fixed leaves no trace here)
                                "trace_id": str?, "span_id": str?}  # ids only when tracing is on
      {"type": "error",         "message": str}  # on exception, always followed by done

    ``agent`` names the sub-agent whose step the event is (an agent used as a tool);
    absent = the root agent. ``index`` is unique across the agents of a run.
    """
    from backend.agent import AGENT_TOOLS, SKILL_NAME, SYSTEM_PROMPT, create_agent, turn_agent_report, turn_entry_names
    from backend.context import next_turn

    start_time = time.time()

    def elapsed_ms() -> int:
        return int((time.time() - start_time) * 1000)

    # --- Accumulated data for query log ---
    _log: dict = {
        "provider": PROVIDER_LABEL,
        "models_tried": [],
        "elapsed_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "llm_calls": 0,
        "ttft_ms": None,
        "tool_calls_count": 0,
        "tool_names": [],
        "status": "error",
        "error_message": None,
    }

    # Trace events from the model wrapper and the tool hooks, in the order they happen.
    # Stamped when they happen, delivered between stream events.
    pending: list[dict] = []
    models_tried: list[str] = []
    llm_calls = 0
    # Time to first streamed text delta, i.e. what the user perceives as latency.
    ttft_ms: int | None = None

    def queue(evt: dict) -> None:
        # Strands yields nothing while a model call waits for its first byte or a
        # tool runs, so a drain-time stamp would hide that time.
        pending.append({**evt, "timestamp_ms": elapsed_ms()})

    def on_model_event(evt: dict) -> None:
        nonlocal llm_calls
        if evt["type"] == "llm_call":
            llm_calls += 1
            evt = {**evt, "call_index": llm_calls}
        elif "agent" not in evt:
            # A sub-agent's model is not a fallback of the root's: it stays out of models_tried.
            models_tried.append(evt["model"])
        queue(evt)

    def drain():
        while pending:
            yield _sse(pending.pop(0))

    tool_trace = _ToolTrace(queue)

    logger.info("[REQUEST] ─── Nueva consulta (conversación %s) ───", conversation_id)
    logger.info("[REQUEST] Query: %.120s%s", query, "…" if len(query) > 120 else "")

    yield _sse({"type": "start", "provider": PROVIDER_LABEL, "conversation_id": conversation_id})

    def done_event(usage: dict) -> dict:
        return {
            "type": "done",
            "total_tool_calls": tool_trace.count,
            "elapsed_ms": elapsed_ms(),
            "models_tried": list(dict.fromkeys(models_tried)),
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "total_tokens": usage.get("totalTokens", 0),
            # Populated by Strands from usage.prompt_tokens_details.cached_tokens.
            "cached_tokens": usage.get("cacheReadInputTokens", 0),
            "llm_calls": llm_calls,
            "ttft_ms": ttft_ms,
            "guard": guard.reason if guard else None,
        }

    agent = None
    agent_span_opened = False
    guard: guards.StreamGuard | None = None
    async with conversation_lock(conversation_id):
        try:
            logger.info("[MCP] Conectando al servidor MCP y restaurando la sesión…")
            agent = await asyncio.to_thread(
                create_agent,
                conversation_id,
                authenticated=authenticated,
                trace=on_model_event,
                hooks=[tool_trace],
                trainer_id=trainer_id,
                sub_agent_hooks=lambda name: [tool_trace.for_sub_agent(name)],
            )
            tool_names = list(agent.tool_names)
            turn = next_turn(agent)
            yield _sse(
                {
                    "type": "agent_init",
                    "mcp_tools_count": len([name for name in tool_names if name not in AGENT_TOOLS]),
                    "tool_names": tool_names,
                    "skill_loaded": SKILL_NAME,
                    "system_prompt_preview": SYSTEM_PROMPT.strip()[:200],
                    "history_messages": len(agent.messages),
                    "turn": turn,
                    "timestamp_ms": elapsed_ms(),
                }
            )

            guard = guards.StreamGuard(
                SYSTEM_PROMPT,
                query,
                entries=lambda: turn_entry_names(agent.messages),
                report=lambda: turn_agent_report(agent.messages),
            )
            with telemetry.agent_span(conversation_id) as agent_obs:
                agent_span_opened = True
                prompt = await _expand_prompt(query)

                async for event in agent.stream_async(prompt):
                    for frame in drain():
                        yield frame

                    # A new model message: after a retried call (agent.GuardReply), noise the
                    # guard held back must not prefix the new answer.
                    if "messageStart" in (event.get("event") or {}):
                        guard.restart()

                    text_delta = event.get("data")
                    if text_delta:
                        # Model TTFT: the guard may hold the first characters back a little longer.
                        if ttft_ms is None:
                            ttft_ms = elapsed_ms()
                        if shown := guard.feed(text_delta):
                            yield _sse({"type": "text", "delta": shown})

                    if event.get("result") is not None:
                        for frame in drain():
                            yield frame
                        if tail := guard.finish():
                            yield _sse({"type": "text", "delta": tail})
                        if guard.reason:
                            logger.warning("[GUARD] Respuesta reemplazada o cortada: %s", guard.reason)
                        usage: dict = {}
                        with suppress(Exception):
                            usage = _run_usage(event["result"])
                        done = done_event(usage)
                        logger.info(
                            "[DONE] %dms (TTFT %sms) | llamadas LLM: %d | tools: %d | tokens: %d (cache: %d) | modelos: %s",
                            done["elapsed_ms"],
                            done["ttft_ms"] if done["ttft_ms"] is not None else "—",
                            done["llm_calls"],
                            done["total_tool_calls"],
                            done["total_tokens"],
                            done["cached_tokens"],
                            ", ".join(done["models_tried"]) or "—",
                        )
                        _log.update(
                            models_tried=done["models_tried"],
                            elapsed_ms=done["elapsed_ms"],
                            input_tokens=done["input_tokens"],
                            output_tokens=done["output_tokens"],
                            total_tokens=done["total_tokens"],
                            cached_tokens=done["cached_tokens"],
                            llm_calls=done["llm_calls"],
                            ttft_ms=done["ttft_ms"],
                            tool_calls_count=done["total_tool_calls"],
                            tool_names=tool_trace.names,
                            status="done",
                        )
                        telemetry.annotate(
                            agent_obs,
                            input_data=query,
                            output_data=guard.shown,
                            # Timed from request arrival (MCP connect included), not from span start.
                            metadata={
                                "request_elapsed_ms": done["elapsed_ms"],
                                "request_ttft_ms": done["ttft_ms"],
                                "llm_calls": done["llm_calls"],
                                "models_tried": done["models_tried"],
                                # Compaction bounds the history, so the turn tells how deep the session is.
                                "turn": turn,
                                "history_messages": len(agent.messages),
                                "guard": guard.reason,
                            },
                            metrics={
                                "input_tokens": done["input_tokens"],
                                "output_tokens": done["output_tokens"],
                                "total_tokens": done["total_tokens"],
                                "cache_read_input_tokens": done["cached_tokens"],
                            },
                            tags={"authenticated": str(authenticated).lower()},
                        )
                        ids = telemetry.export_ids(agent_obs)
                        if ids:
                            done.update(ids)  # lets the frontend tie 👍/👎 to this trace
                            feedback_registry.register(ids["span_id"], ids["trace_id"], conversation_id)
                        yield _sse(done)
                        # Rule evals run after `done` (no effect on elapsed/TTFT) and only
                        # enqueue. If the client disconnected, GeneratorExit is raised at
                        # the yield above and they are skipped.
                        if ids:
                            _submit_rule_evals(ids, query, guard.shown, tool_trace, agent.messages)

        except Exception as exc:
            logger.exception("Agent run failed: %s", exc)
            if not agent_span_opened:
                # Failed before the agent span opened (MCP connect, session restore): still count it.
                telemetry.record_failed_request(conversation_id, exc, input_data=query)
            for frame in drain():
                yield frame
            _log.update(
                models_tried=list(dict.fromkeys(models_tried)),
                elapsed_ms=elapsed_ms(),
                llm_calls=llm_calls,
                ttft_ms=ttft_ms,
                tool_calls_count=tool_trace.count,
                tool_names=tool_trace.names,
                status="error",
                error_message=str(exc),
            )
            yield _sse({"type": "error", "message": "Error interno del agente. Intenta de nuevo más tarde."})
            # Ensure done is always sent so the frontend can re-enable input
            yield _sse(done_event({}))
        finally:
            if agent is not None:
                await asyncio.to_thread(agent.cleanup)
            log_query(query=query, **_log)


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------


async def _verify_admin(api_key: str = Security(_api_key_header)) -> None:
    """Reject admin API calls without the admin key (see ``_is_valid_admin_key``); no key configured = all rejected."""
    if not _is_valid_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Admin access denied")


@app.get("/admin", include_in_schema=False)
async def admin_panel():
    # The HTML page itself is public; auth is enforced by the /admin/api/* endpoints.
    # admin.html handles its own login prompt using sessionStorage.
    path = os.path.join(os.path.dirname(__file__), "static", "admin.html")
    return FileResponse(path, media_type="text/html")


@app.get("/admin/api/stats", dependencies=[Depends(_verify_admin)], include_in_schema=False)
async def admin_stats():
    return get_stats()


@app.get("/admin/api/queries", dependencies=[Depends(_verify_admin)], include_in_schema=False)
async def admin_queries(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(""),
):
    return get_queries(page=page, limit=limit, search=search)


@app.get("/admin/traces", include_in_schema=False)
async def admin_traces_page():
    # Public HTML like /admin: the data comes from the /admin/api/traces endpoints below.
    path = os.path.join(os.path.dirname(__file__), "static", "traces.html")
    return FileResponse(path, media_type="text/html")


def _trace_reader() -> traces.PhoenixReader:
    reader = traces.reader()
    if reader is None:
        raise HTTPException(status_code=503, detail="Phoenix no está configurado en este servidor.")
    return reader


def _read_phoenix(call):
    """Run a PhoenixReader call; Phoenix being down or slow is a 502, not a crash."""
    try:
        return call()
    except httpx.HTTPError as exc:
        logger.warning("[TRACES] Phoenix no respondió: %s", exc)
        raise HTTPException(status_code=502, detail="Phoenix no respondió. Intenta de nuevo.") from exc


# Sync handlers: the reader blocks on HTTP, so FastAPI runs them in its thread pool.
@app.get("/admin/api/traces", dependencies=[Depends(_verify_admin)], include_in_schema=False)
def admin_traces(limit: int = Query(30, ge=1, le=traces.MAX_TRACES)):
    """Recent queries, newest first, for the run view."""
    reader = _trace_reader()
    return {"traces": _read_phoenix(lambda: reader.list_traces(limit))}


@app.get("/admin/api/traces/{trace_id}", dependencies=[Depends(_verify_admin)], include_in_schema=False)
def admin_trace(trace_id: str):
    """One query with every step of its run."""
    if not traces.TRACE_ID_RE.fullmatch(trace_id):
        raise HTTPException(status_code=404, detail="No existe esa consulta.")
    reader = _trace_reader()
    trace = _read_phoenix(lambda: reader.get_trace(trace_id))
    if trace is None:
        raise HTTPException(status_code=404, detail="No existe esa consulta.")
    return trace


# ---------------------------------------------------------------------------
# Static file serving — must be mounted AFTER all API routes
# ---------------------------------------------------------------------------

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
