"""
FastAPI application for the Pokemon MCP Agent.

Routes:
  GET  /health             — health check + active providers
  GET  /providers          — list all providers with availability
  POST /api/agent/run      — SSE stream: agent response + tool call trace
  GET  /                   — serves index.html (via StaticFiles mount)

TODO: Add authentication before exposing to the public internet.
TODO: Add rate limiting (e.g. slowapi) to prevent abuse.
TODO: Restrict CORS origins to your production domain.
"""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.providers import PROVIDERS, build_litellm_router, get_available_providers

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate at startup that at least one provider is configured.
    # This will raise RuntimeError if all providers are missing.
    try:
        _router, label = build_litellm_router()
        available = get_available_providers()
        logger.info(
            "Active LLM providers: %s",
            ", ".join(p["label"] for p in available),
        )
        logger.info("Primary provider (auto-fallback): %s", label)
    except RuntimeError as exc:
        logger.error("Startup error: %s", exc)
        raise
    yield


app = FastAPI(title="Pokemon MCP Agent", lifespan=lifespan)

# TODO: Restrict to specific origins before going to production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    query: str
    provider: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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
async def get_prompts():
    """Fetch the list of prompts from the MCP server."""
    from backend.agent import build_agent
    _model, mcp_client, _label = build_agent()
    try:
        with mcp_client:
            mcp_client.start()
            prompts_result = mcp_client.list_prompts_sync()
            # prompts_result is a ListPromptsResult object (from mcp-sdk)
            # We want to return a clean list for the frontend
            return [
                {
                    "name": p.name,
                    "description": p.description or "",
                    "arguments": [
                        {
                            "name": arg.name,
                            "description": arg.description or "",
                            "required": arg.required,
                        }
                        for arg in (p.arguments or [])
                    ],
                }
                for p in prompts_result.prompts
            ]
    except Exception as exc:
        logger.error("Error fetching prompts: %s", exc)
        return []


@app.post("/api/agent/run")
async def run_agent(body: RunRequest):
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

    def elapsed_ms() -> int:
        return int((time.time() - start_time) * 1000)

    try:
        model, mcp_client, provider_label = build_agent(provider)
    except RuntimeError as exc:
        yield _sse({"type": "error", "message": str(exc), "fallback_available": False})
        return

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
            yield _sse(
                {
                    "type": "tool_result",
                    "index": idx,
                    "tool": tool_names.get(idx, "unknown"),
                    "result": result_text,
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
            mcp_client.start()
            tools = mcp_client.list_tools_sync()

            # --- Check if query is a prompt call ---
            # Pattern: "Usa el prompt <name> [con <arg>=<val>...]"
            import re
            m = re.match(r"(?:usa el prompt|ejecuta el prompt|aplicar el prompt)\s+([a-zA-Z0-9_-]+)(?:\s+con\s+(.+))?", query, re.I)
            if m:
                prompt_name = m.group(1)
                args_str = m.group(2) or ""
                args = {}
                if args_str:
                    # Simple key=value parser
                    for pair in re.split(r",\s*", args_str):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            args[k.strip()] = v.strip()
                
                try:
                    prompt_res = mcp_client.get_prompt_sync(prompt_name, args)
                    # A prompt result usually contains a list of messages.
                    # We inject these into the agent as the starting point.
                    prompt_text = "\n".join(
                        msg.content.text if hasattr(msg.content, "text") else str(msg.content)
                        for msg in prompt_res.messages
                    )
                    query = f"I am using the MCP prompt '{prompt_name}' with these instructions:\n\n{prompt_text}\n\nPlease follow these instructions and provide the final answer."
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
                    yield _sse(
                        {
                            "type": "done",
                            "total_tool_calls": tool_index,
                            "elapsed_ms": elapsed_ms(),
                            "models_tried": list(dict.fromkeys(_all_model_attempts)),
                        }
                    )

    except Exception as exc:
        logger.exception("Agent run failed: %s", exc)
        available = get_available_providers()
        fallback_available = len(available) > 1 or (
            provider is not None and len(available) >= 1
        )
        yield _sse(
            {
                "type": "error",
                "message": str(exc),
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
            }
        )
    finally:
        _cv_model_events.reset(_me_token)
        _cv_llm_prompts.reset(_lp_token)


# ---------------------------------------------------------------------------
# Static file serving — must be mounted AFTER all API routes
# ---------------------------------------------------------------------------

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
