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
  - Rate limiting: /api/agent/run is capped at 3 req/min per IP via slowapi.
  - Feedback: /api/feedback takes 10 req/min per IP, one vote per reply, and
    only for replies this process issued (in-memory registry, single worker).
  - CORS: controlled by ALLOWED_ORIGINS env var (comma-separated).
    Defaults to "*" when unset (local dev only — set a real domain in prod).
"""

import asyncio
import hmac
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from typing import Any, Optional

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

from backend import build_info, mcp_gateway, telemetry, tool_cache
from backend.database import get_queries, get_stats, init_db, log_query
from backend.evals import checks
from backend.feedback import registry as feedback_registry
from backend.providers import API_KEY_ENV, PROVIDER_LABEL, groq_configured
from backend.sessions import CONVERSATION_ID_RE, conversation_lock, prune_periodically
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


def _is_valid_api_key(api_key: str | None) -> bool:
    """Return True if the provided key matches the configured API_KEY."""
    expected = os.environ.get("API_KEY", "").strip()
    if not expected or not api_key:
        return False
    return hmac.compare_digest(api_key, expected)


def _is_authenticated_request() -> bool:
    return _is_valid_api_key(_req_api_key.get())


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
    init_db()
    prune_task = asyncio.create_task(prune_periodically())
    try:
        yield
    finally:
        prune_task.cancel()
        with suppress(asyncio.CancelledError):
            await prune_task


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
@limiter.limit("3/minute", exempt_when=_is_authenticated_request)
async def run_agent(request: Request, body: RunRequest, api_key: str = Security(_api_key_header)):
    authenticated = _is_valid_api_key(api_key)
    # Every yielded SSE frame must reach the client as its own chunk (test_streaming.py).
    return StreamingResponse(
        _sse_generator(body.query, body.conversation_id, authenticated=authenticated),
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
    """

    def __init__(self, emit) -> None:
        self._emit = emit
        self._next_index = 0
        self._index_by_id: dict[str, int] = {}
        self._spans: dict[str, Any] = {}
        self.names: list[str] = []
        # Raw results (not the summarized display) for the quality checks.
        self.observations: list[checks.ToolObservation] = []

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._on_call)
        registry.add_callback(AfterToolCallEvent, self._on_result)

    def _on_call(self, event) -> None:
        uid = event.tool_use.get("toolUseId", "")
        name = event.tool_use.get("name", "unknown")
        index = self._next_index
        self._next_index += 1
        self._index_by_id[uid] = index
        self._spans[uid] = telemetry.start_tool_span(name)
        self.names.append(name)
        args = dict(event.tool_use.get("input") or {})
        logger.info("[TOOL] Llamando tool #%d: %s | args: %s", index, name, str(args)[:120])
        self._emit({"type": "tool_call", "index": index, "tool_id": uid, "tool": name, "args": args})

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
        self._emit({"type": "tool_result", "index": index, "tool_id": uid, "tool": name, "result": display,
                    "cached": cache in ("hit", "stale"), "duration_ms": duration_ms})
        telemetry.finish_tool_span(
            self._spans.pop(uid, None),
            input_data=dict(event.tool_use.get("input") or {}),
            output_data=display,
            error=event.result.get("status") == "error",
            tags={"cache": cache} if cache else None,
        )
        self.observations.append(
            checks.ToolObservation(
                name=name,
                args=dict(event.tool_use.get("input") or {}),
                text=text,
                error=event.result.get("status") == "error",
            )
        )

        uri = mcp_gateway.ui_resource_uri(getattr(event.selected_tool, "mcp_tool", None))
        if uri and event.result.get("status") != "error":
            self._emit(
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
        return self._next_index


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


async def _sse_generator(query: str, conversation_id: str, authenticated: bool = False):
    """
    Async generator that yields SSE events for one agent run.

    Event sequence:
      {"type": "start",         "provider": str, "conversation_id": str}
      {"type": "agent_init",    "mcp_tools_count": int, "tool_names": list, "skill_loaded": str, ...}
                                # skill_loaded: the narration protocol, always inline in SYSTEM_PROMPT
                                # turn: 1-based turn of the conversation; history_messages: after the last compaction
      {"type": "llm_call",      "call_index": int, "model": str, "messages": list, "timestamp_ms": int,
                                "call_role": "routing"|"final", "reasoning_effort": str}
                                # routing: opens the turn (picks a tool or answers directly); final: after tool results
      {"type": "model_attempt", "model": str, "status": "success"|"failed", "error": str?, "timestamp_ms": int}
      {"type": "tool_call",     "index": int, "tool_id": str, "tool": str, "args": dict, "timestamp_ms": int}
      {"type": "tool_result",   "index": int, "tool_id": str, "tool": str, "result": str, "timestamp_ms": int,
                                "cached": bool, "duration_ms": float | None}
      {"type": "ui_resource",   "index": int, "tool_id": str, "tool": str, "resource_uri": str,
                                "tool_input": dict, "tool_result": CallToolResult, "timestamp_ms": int}
      {"type": "text",          "delta": str}
      {"type": "done",          "total_tool_calls": int, "elapsed_ms": int, "models_tried": list[str], tokens…,
                                "cached_tokens": int, "llm_calls": int, "ttft_ms": int | None,
                                "trace_id": str?, "span_id": str?}  # ids only when tracing is on
      {"type": "error",         "message": str}  # on exception, always followed by done
    """
    from backend.agent import SKILL_NAME, SYSTEM_PROMPT, create_agent
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
        else:
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
        }

    agent = None
    agent_span_opened = False
    async with conversation_lock(conversation_id):
        try:
            logger.info("[MCP] Conectando al servidor MCP y restaurando la sesión…")
            agent = await asyncio.to_thread(
                create_agent,
                conversation_id,
                authenticated=authenticated,
                trace=on_model_event,
                hooks=[tool_trace],
            )
            tool_names = list(agent.tool_names)
            turn = next_turn(agent)
            yield _sse(
                {
                    "type": "agent_init",
                    "mcp_tools_count": len(tool_names),
                    "tool_names": tool_names,
                    "skill_loaded": SKILL_NAME,
                    "system_prompt_preview": SYSTEM_PROMPT.strip()[:200],
                    "history_messages": len(agent.messages),
                    "turn": turn,
                    "timestamp_ms": elapsed_ms(),
                }
            )

            reply: list[str] = []
            with telemetry.agent_span(conversation_id) as agent_obs:
                agent_span_opened = True
                prompt = await _expand_prompt(query)

                async for event in agent.stream_async(prompt):
                    for frame in drain():
                        yield frame

                    text_delta = event.get("data")
                    if text_delta:
                        if ttft_ms is None:
                            ttft_ms = elapsed_ms()
                        reply.append(text_delta)
                        yield _sse({"type": "text", "delta": text_delta})

                    if event.get("result") is not None:
                        for frame in drain():
                            yield frame
                        usage: dict = {}
                        with suppress(Exception):
                            usage = event["result"].metrics.accumulated_usage or {}
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
                            output_data="".join(reply),
                            # Timed from request arrival (MCP connect included), not from span start.
                            metadata={
                                "request_elapsed_ms": done["elapsed_ms"],
                                "request_ttft_ms": done["ttft_ms"],
                                "llm_calls": done["llm_calls"],
                                "models_tried": done["models_tried"],
                                # Compaction bounds the history, so the turn tells how deep the session is.
                                "turn": turn,
                                "history_messages": len(agent.messages),
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
                            _submit_rule_evals(ids, query, "".join(reply), tool_trace, agent.messages)

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
    """Reject admin API calls when API_KEY is set but header is missing/wrong."""
    if not _is_valid_api_key(api_key):
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


# ---------------------------------------------------------------------------
# Static file serving — must be mounted AFTER all API routes
# ---------------------------------------------------------------------------

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
