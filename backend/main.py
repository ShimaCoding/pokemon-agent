"""
FastAPI application for the Pokemon MCP Agent.

Routes:
  GET  /health             — health check + active providers
  GET  /providers          — list all providers with availability
  GET  /config             — public frontend config (e.g. require_api_key flag)
  POST /api/agent/run      — SSE stream: agent response + tool call trace
  GET  /                   — serves index.html (via StaticFiles mount)

Security:
  - API key auth: set API_KEY env var; clients must send X-API-Key header.
    If API_KEY is unset, auth is disabled (suitable for local dev).
  - Rate limiting: /api/agent/run is capped at 3 req/min per IP via slowapi.
  - CORS: controlled by ALLOWED_ORIGINS env var (comma-separated).
    Defaults to "*" when unset (local dev only — set a real domain in prod).
"""

import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.database import get_queries, get_stats, init_db, log_query
from backend.providers import PROVIDERS, build_litellm_router, get_available_providers

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


async def require_api_key(api_key: str = Security(_api_key_header)) -> None:
    """Dependency: require a valid API key (used for admin endpoints)."""
    if not _is_valid_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate at startup that at least one provider is configured.
    # This will raise RuntimeError if all providers are missing.
    try:
        _router, _primary_model, label = build_litellm_router()
        available = get_available_providers()
        logger.info(
            "Active LLM providers: %s",
            ", ".join(p["label"] for p in available),
        )
        logger.info("Primary provider (auto-fallback): %s", label)
        if _cors_origins == ["*"]:
            logger.warning(
                "CORS configured with wildcard '*'. "
                "Set ALLOWED_ORIGINS in .env for production."
            )
    except RuntimeError as exc:
        logger.error("Startup error: %s", exc)
        raise
    init_db()
    yield


app = FastAPI(title="Pokemon MCP Agent", lifespan=lifespan)

app.state.limiter = limiter


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    from fastapi.responses import JSONResponse
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
    provider: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/config")
async def config():
    """Public endpoint: frontend config flags."""
    return {"require_api_key": bool(os.environ.get("API_KEY", "").strip())}


@app.get("/health")
async def health():
    available = get_available_providers()
    return {"status": "ok", "providers": [p["label"] for p in available]}


@app.get("/providers")
async def providers():
    result = []
    for name, config in PROVIDERS.items():
        api_key = os.environ.get(config["env_var"], "").strip()
        result.append(
            {
                "name": name,
                "label": config["label"],
                "available": bool(api_key),
            }
        )
    # Sort by order so the frontend renders them in priority order
    result.sort(key=lambda p: PROVIDERS[p["name"]]["order"])
    return result


@app.get("/api/prompts")
@limiter.limit("10/minute")
async def get_prompts(request: Request):
    """Fetch the list of prompts from the MCP server."""
    import asyncio
    from backend.agent import build_agent

    def _fetch_sync():
        _model, mcp_client, _label = build_agent()
        with mcp_client:
            prompts_result = mcp_client.list_prompts_sync()
            return [
                {
                    "name": p.name,
                    "description": p.description or "",
                    "arguments": [
                        {
                            "name": arg.name,
                            "description": arg.description or "",
                            "required": bool(arg.required),
                        }
                        for arg in (p.arguments or [])
                    ],
                }
                for p in prompts_result.prompts
            ]

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _fetch_sync)
    except Exception as exc:
        logger.error("Error fetching prompts: %s", exc)
        return []


@app.get("/api/tools")
@limiter.limit("10/minute")
async def get_tools(request: Request):
    """Fetch the list of tools from the MCP server."""
    import asyncio
    from backend.agent import build_agent

    def _fetch_sync():
        _model, mcp_client, _label = build_agent()
        with mcp_client:
            tools_result = mcp_client.list_tools_sync()
            return [
                {
                    "name": t.mcp_tool.name,
                    "description": t.mcp_tool.description or "",
                    "input_schema": t.mcp_tool.inputSchema if hasattr(t.mcp_tool, "inputSchema") else {},
                }
                for t in tools_result
            ]

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _fetch_sync)
    except Exception as exc:
        logger.error("Error fetching tools: %s", exc)
        return []


@app.get("/api/resources")
@limiter.limit("10/minute")
async def get_resources(request: Request):
    """Fetch the list of resources from the MCP server."""
    import asyncio
    from backend.agent import build_agent

    def _fetch_sync():
        _model, mcp_client, _label = build_agent()
        with mcp_client:
            resources_result = mcp_client.list_resources_sync()
            return [
                {
                    "uri": r.uri,
                    "name": r.name or "",
                    "description": r.description or "",
                    "mime_type": r.mimeType or "",
                }
                for r in resources_result.resources
            ]

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _fetch_sync)
    except Exception as exc:
        logger.error("Error fetching resources: %s", exc)
        return []


@app.post("/api/agent/run")
@limiter.limit("3/minute", exempt_when=lambda: _is_valid_api_key(_req_api_key.get()))
async def run_agent(request: Request, body: RunRequest):
    return StreamingResponse(
        _sse_generator(body.query, body.provider),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------


def _sse(payload: dict) -> str:
    """Format a dict as an SSE data frame."""
    return f"data: {json.dumps(payload)}\n\n"


def _extract_result_text(tool_result: dict) -> str:
    """Extract plain text from a Strands ToolResult dict."""
    content = tool_result.get("content", [])
    if isinstance(content, list):
        return " ".join(c.get("text", str(c)) for c in content)
    return str(content)


class _ToolHook:
    """
    Strands HookProvider that captures tool calls and results via hooks.

    BeforeToolCallEvent / AfterToolCallEvent are used instead of streaming
    current_tool_use detection because the streaming approach is unreliable:
    current_tool_use["input"] is a string during streaming and only becomes a
    dict after the contentBlockStop mutation, which happens after the consumer
    has already processed those events.  Hooks fire with the fully-parsed ToolUse
    at the correct moment and are the reliable way to capture this data.
    """

    def register_hooks(self, registry) -> None:
        from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._capture_call)
        registry.add_callback(AfterToolCallEvent, self._capture_result)

    def __init__(self) -> None:
        # toolUseId → (name, input_dict), populated before each tool runs
        self._pending_calls: dict[str, tuple[str, dict]] = {}
        # toolUseId → result text, populated after each tool runs
        self._pending_results: dict[str, str] = {}

    def _capture_call(self, event) -> None:
        uid = event.tool_use.get("toolUseId", "")
        if uid:
            self._pending_calls[uid] = (
                event.tool_use.get("name", "unknown"),
                dict(event.tool_use.get("input") or {}),
            )

    def _capture_result(self, event) -> None:
        uid = event.tool_use.get("toolUseId", "")
        if uid:
            self._pending_results[uid] = _extract_result_text(event.result)

    def pending_call_ids(self) -> list[str]:
        return list(self._pending_calls.keys())

    def pop_call(self, uid: str) -> Optional[tuple[str, dict]]:
        return self._pending_calls.pop(uid, None)

    def pop_result(self, uid: str) -> Optional[str]:
        return self._pending_results.pop(uid, None)


async def _sse_generator(query: str, provider: Optional[str] = None):
    """
    Async generator that yields SSE events for one agent run.

    Event sequence:
      {"type": "start",         "provider": str}
      {"type": "llm_call",      "call_index": int, "messages": list, "timestamp_ms": int}
      {"type": "model_attempt", "model": str, "status": "success"|"failed", "error": str?, "timestamp_ms": int}
      {"type": "tool_call",     "index": int, "tool": str, "args": dict, "timestamp_ms": int}
      {"type": "tool_result",   "index": int, "tool": str, "result": str, "timestamp_ms": int}
      {"type": "text",          "delta": str}
      {"type": "done",          "total_tool_calls": int, "elapsed_ms": int, "models_tried": list[str]}
      {"type": "error",         "message": str, "fallback_available": bool}  # on exception
    """
    from backend.agent import _llm_prompts as _cv_llm_prompts
    from backend.agent import _model_events as _cv_model_events
    from backend.agent import build_agent, create_agent

    start_time = time.time()

    # --- Accumulated data for query log ---
    _log: dict = {
        "provider": None,
        "models_tried": [],
        "elapsed_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "tool_calls_count": 0,
        "tool_names": [],
        "status": "error",
        "error_message": None,
    }

    def elapsed_ms() -> int:
        return int((time.time() - start_time) * 1000)

    logger.info("[REQUEST] ─── Nueva consulta recibida ───────────────────────")
    logger.info("[REQUEST] Query: %.120s%s", query, "…" if len(query) > 120 else "")

    try:
        model, mcp_client, provider_label = build_agent(provider)
    except RuntimeError as exc:
        logger.error("[PROVIDER] Sin providers disponibles: %s", exc)
        yield _sse({"type": "error", "message": str(exc), "fallback_available": False})
        return

    logger.info("[PROVIDER] Provider seleccionado: %s", provider_label)
    _log["provider"] = provider_label

    # Per-request queues populated by agent.py's monkey-patched litellm calls.
    _me_queue: list = []  # model_attempt events
    _lp_queue: list = []  # llm_call events
    _all_model_attempts: list[str] = []  # accumulates all model IDs tried (for done event)
    _me_token = _cv_model_events.set(_me_queue)
    _lp_token = _cv_llm_prompts.set(_lp_queue)

    yield _sse({"type": "start", "provider": provider_label})

    tool_index = 0
    # Maps toolUseId → stream index so results can be matched back to calls.
    tool_use_id_to_index: dict[str, int] = {}
    # Maps stream index → tool_name for result events.
    tool_names: dict[int, str] = {}
    # Track which indices have had their result emitted.
    emitted_results: set[int] = set()

    tool_hook = _ToolHook()

    async def _drain_results():
        """Yield SSE frames for any tool results collected by AfterToolCallEvent."""
        for uid, idx in list(tool_use_id_to_index.items()):
            if idx in emitted_results:
                continue
            result_text = tool_hook.pop_result(uid)
            if result_text is None:
                continue
            emitted_results.add(idx)
            t_name = tool_names.get(idx, "unknown")
            logger.info("[TOOL] Resultado de %s: %.120s%s", t_name, result_text, "…" if len(result_text) > 120 else "")
            # Skill instructions are internal scaffolding — show a brief summary
            # instead of the full SKILL.md text in the trace panel.
            display_result = (
                f"[Skill activado: instrucciones de narración cargadas ({len(result_text)} chars)]"
                if t_name == "skills"
                else result_text
            )
            yield _sse(
                {
                    "type": "tool_result",
                    "index": idx,
                    "tool": t_name,
                    "result": display_result,
                    "timestamp_ms": elapsed_ms(),
                }
            )

    async def _drain_llm_events():
        """Yield SSE frames for llm_call and model_attempt events collected by the monkey-patch."""
        while _lp_queue:
            evt = _lp_queue.pop(0)
            evt["timestamp_ms"] = elapsed_ms()
            yield _sse(evt)
        while _me_queue:
            evt = _me_queue.pop(0)
            evt["timestamp_ms"] = elapsed_ms()
            _all_model_attempts.append(evt["model"])
            yield _sse(evt)

    try:
        with mcp_client:
            logger.info("[MCP] Conectando al servidor MCP y listando tools…")
            tools = mcp_client.list_tools_sync()
            logger.info("[MCP] %d tools disponibles", len(tools))

            # --- Check if query is a prompt call ---
            # Pattern: "Usa el prompt <name> [con <arg>=<val>...]"
            import re, json as _json
            m = re.match(r"(?:usa el prompt|ejecuta el prompt|aplicar el prompt)\s+([a-zA-Z0-9_./-]+)(?:\s+con\s+(.+))?", query, re.I)
            if m:
                prompt_name = m.group(1)
                args_str = m.group(2) or ""
                args = {}
                if args_str:
                    # Accept both comma-separated and space-separated key=value pairs
                    for pair in re.split(r"[,\s]+", args_str):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            args[k.strip()] = v.strip()

                try:
                    prompt_res = mcp_client.get_prompt_sync(prompt_name, args)
                    # Extract the actual prompt text.
                    # This MCP server wraps its instructions in a nested JSON object
                    # inside msg.content.text, so we unwrap it when possible.
                    parts: list[str] = []
                    for msg in prompt_res.messages:
                        raw = msg.content.text if hasattr(msg.content, "text") else str(msg.content)
                        try:
                            blob = _json.loads(raw)
                            for inner in blob.get("messages", []):
                                txt = inner.get("content", {}).get("text", "")
                                if txt:
                                    parts.append(txt)
                        except Exception:
                            parts.append(raw)
                    prompt_text = "\n".join(parts)
                    prompt_text = prompt_text[:2000]
                    query = f"Using MCP prompt '{prompt_name}':\n\n{prompt_text}\n\nPlease follow these instructions and provide the complete answer using the available tools."
                except Exception as p_exc:
                    logger.warning(f"Could not fetch prompt {prompt_name}: {p_exc}")

            agent = create_agent(model, tools, hooks=[tool_hook])

            async for event in agent.stream_async(query):
                # --- Drain LLM prompt + model attempt events ---
                async for frame in _drain_llm_events():
                    yield frame

                # --- Drain tool calls captured by BeforeToolCallEvent hook ---
                for uid in tool_hook.pending_call_ids():
                    call = tool_hook.pop_call(uid)
                    if call is None:
                        continue
                    t_name, t_args = call
                    tool_use_id_to_index[uid] = tool_index
                    tool_names[tool_index] = t_name
                    logger.info("[TOOL] Llamando tool #%d: %s | args: %s", tool_index, t_name, str(t_args)[:120])
                    yield _sse(
                        {
                            "type": "tool_call",
                            "index": tool_index,
                            "tool": t_name,
                            "args": t_args,
                            "timestamp_ms": elapsed_ms(),
                        }
                    )
                    tool_index += 1

                # --- Drain tool results captured by AfterToolCallEvent hook ---
                async for frame in _drain_results():
                    yield frame

                # --- Text delta events ---
                text_delta = event.get("data")
                if text_delta:
                    yield _sse({"type": "text", "delta": text_delta})

                # --- Completion event ---
                if event.get("result") is not None:
                    # Final drain before done
                    async for frame in _drain_llm_events():
                        yield frame
                    async for frame in _drain_results():
                        yield frame

                    # Extract token usage from Strands AgentResult metrics
                    _result = event["result"]
                    _usage: dict = {}
                    try:
                        _usage = _result.metrics.accumulated_usage or {}
                    except Exception:
                        pass

                    _elapsed = elapsed_ms()
                    _models_tried = list(dict.fromkeys(_all_model_attempts))
                    logger.info(
                        "[DONE] Completado en %dms | tools usadas: %d | tokens: %d | modelos: %s",
                        _elapsed,
                        tool_index,
                        _usage.get("totalTokens", 0),
                        ", ".join(_models_tried) or "—",
                    )
                    _log.update(
                        models_tried=_models_tried,
                        elapsed_ms=_elapsed,
                        input_tokens=_usage.get("inputTokens", 0),
                        output_tokens=_usage.get("outputTokens", 0),
                        total_tokens=_usage.get("totalTokens", 0),
                        tool_calls_count=tool_index,
                        tool_names=[tool_names[i] for i in sorted(tool_names)],
                        status="done",
                    )
                    yield _sse(
                        {
                            "type": "done",
                            "total_tool_calls": tool_index,
                            "elapsed_ms": _elapsed,
                            "models_tried": _models_tried,
                            "input_tokens": _usage.get("inputTokens", 0),
                            "output_tokens": _usage.get("outputTokens", 0),
                            "total_tokens": _usage.get("totalTokens", 0),
                        }
                    )

    except Exception as exc:
        logger.exception("Agent run failed: %s", exc)
        available = get_available_providers()
        fallback_available = len(available) > 1 or (
            provider is not None and len(available) >= 1
        )
        _log.update(
            models_tried=list(dict.fromkeys(_all_model_attempts)),
            elapsed_ms=elapsed_ms(),
            tool_calls_count=tool_index,
            tool_names=[tool_names[i] for i in sorted(tool_names)],
            status="error",
            error_message=str(exc),
        )
        yield _sse(
            {
                "type": "error",
                "message": "Error interno del agente. Intenta de nuevo más tarde.",
                "fallback_available": fallback_available,
            }
        )
        # Ensure done is always sent so the frontend can re-enable input
        yield _sse(
            {
                "type": "done",
                "total_tool_calls": tool_index,
                "elapsed_ms": elapsed_ms(),
                "models_tried": list(dict.fromkeys(_all_model_attempts)),
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
        )
    finally:
        _cv_model_events.reset(_me_token)
        _cv_llm_prompts.reset(_lp_token)
        log_query(query=query, **_log)


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------


async def _verify_admin(api_key: str = Security(_api_key_header)) -> None:
    """Reject admin API calls when API_KEY is set but header is missing/wrong."""
    if not _is_valid_api_key(api_key):
        raise HTTPException(status_code=401, detail="Admin access denied")


@app.get("/admin", include_in_schema=False, dependencies=[Depends(_verify_admin)])
async def admin_panel():
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
