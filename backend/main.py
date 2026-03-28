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


class _ToolResultHook:
    """
    Strands HookProvider that captures AfterToolCallEvent results.

    ToolResultEvent has is_callback_event=False and is never yielded by
    stream_async, so hooks are the only way to receive tool results.
    Results are stored by toolUseId and consumed by _sse_generator.
    """

    def register_hooks(self, registry) -> None:
        from strands.hooks import AfterToolCallEvent

        registry.add_callback(AfterToolCallEvent, self._capture)

    def __init__(self) -> None:
        # toolUseId → result text, populated by the hook callback
        self._pending: dict[str, str] = {}

    def _capture(self, event) -> None:
        uid = event.tool_use.get("toolUseId", "")
        if uid:
            self._pending[uid] = _extract_result_text(event.result)

    def pop(self, tool_use_id: str) -> Optional[str]:
        return self._pending.pop(tool_use_id, None)


async def _sse_generator(query: str, provider: Optional[str] = None):
    """
    Async generator that yields SSE events for one agent run.

    Event sequence:
      {"type": "start",       "provider": str}
      {"type": "tool_call",   "index": int, "tool": str, "args": dict, "timestamp_ms": int}
      {"type": "tool_result", "index": int, "tool": str, "result": str, "timestamp_ms": int}
      {"type": "text",        "delta": str}
      {"type": "done",        "total_tool_calls": int, "elapsed_ms": int}
      {"type": "error",       "message": str, "fallback_available": bool}  # on exception
    """
    from backend.agent import build_agent, create_agent

    start_time = time.time()

    def elapsed_ms() -> int:
        return int((time.time() - start_time) * 1000)

    try:
        model, mcp_client, provider_label = build_agent(provider)
    except RuntimeError as exc:
        yield _sse({"type": "error", "message": str(exc), "fallback_available": False})
        return

    yield _sse({"type": "start", "provider": provider_label})

    tool_index = 0
    # Maps toolUseId → stream index so hook results can be matched back.
    tool_use_id_to_index: dict[str, int] = {}
    # Maps stream index → tool_name for result events.
    tool_names: dict[int, str] = {}
    # Track which indices have had their result emitted.
    emitted_results: set[int] = set()
    # Track the last-seen toolUseId to deduplicate streaming repeats.
    last_tool_use_id: Optional[str] = None

    result_hook = _ToolResultHook()

    async def _drain_results():
        """Yield SSE frames for any tool results collected by the hook."""
        for uid, idx in list(tool_use_id_to_index.items()):
            if idx in emitted_results:
                continue
            result_text = result_hook.pop(uid)
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

    try:
        with mcp_client:
            tools = mcp_client.list_tools_sync()
            agent = create_agent(model, tools, hooks=[result_hook])

            async for event in agent.stream_async(query):
                # --- Drain any tool results that arrived since the last event ---
                async for frame in _drain_results():
                    yield frame

                # --- Tool call events ---
                tool_use = event.get("current_tool_use", {})
                tool_name = tool_use.get("name")
                tool_input = tool_use.get("input")
                tool_use_id = tool_use.get("toolUseId", "")

                if tool_name and isinstance(tool_input, dict) and tool_use_id:
                    # Emit once per unique toolUseId (input is a dict when fully streamed).
                    if tool_use_id != last_tool_use_id:
                        tool_use_id_to_index[tool_use_id] = tool_index
                        tool_names[tool_index] = tool_name
                        last_tool_use_id = tool_use_id
                        yield _sse(
                            {
                                "type": "tool_call",
                                "index": tool_index,
                                "tool": tool_name,
                                "args": tool_input,
                                "timestamp_ms": elapsed_ms(),
                            }
                        )
                        tool_index += 1

                # --- Text delta events ---
                text_delta = event.get("data")
                if text_delta:
                    yield _sse({"type": "text", "delta": text_delta})

                # --- Completion event ---
                if event.get("result") is not None:
                    # Final drain before done
                    async for frame in _drain_results():
                        yield frame
                    yield _sse(
                        {
                            "type": "done",
                            "total_tool_calls": tool_index,
                            "elapsed_ms": elapsed_ms(),
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
            }
        )


# ---------------------------------------------------------------------------
# Static file serving — must be mounted AFTER all API routes
# ---------------------------------------------------------------------------

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
