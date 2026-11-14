"""
Short-lived MCP connections for the non-agent endpoints.

The browser has no MCP client: widget requests (`callServerTool`) and widget
HTML (`ui://` resources) are fetched here and forwarded to the MCP server.
Everything is synchronous (strands' MCPClient runs its own background loop),
so callers run these functions in a worker thread.

Tool UI metadata and widget HTML are cached for a few minutes: they only
change when the MCP server is redeployed.
"""

import threading
import time
from typing import Any

from mcp.client.streamable_http import streamable_http_client
from strands.tools.mcp import MCPClient

from backend.agent import MCP_SERVER_URL

CACHE_TTL_SECONDS = 300

_cache_lock = threading.Lock()
_ui_tools: tuple[float, dict[str, str]] | None = None
_ui_resources: dict[str, tuple[float, dict[str, Any]]] = {}


def _client() -> MCPClient:
    return MCPClient(lambda: streamable_http_client(MCP_SERVER_URL))


def ui_resource_uri(mcp_tool: Any) -> str | None:
    """The `_meta.ui.resourceUri` a tool declares, if any."""
    meta = getattr(mcp_tool, "meta", None) or {}
    uri = (meta.get("ui") or {}).get("resourceUri")
    return uri if isinstance(uri, str) and uri.startswith("ui://") else None


def to_call_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert a Strands MCPToolResult into the MCP `CallToolResult` wire shape widgets expect."""
    content = []
    for block in result.get("content", []):
        if "text" in block:
            content.append({"type": "text", "text": block["text"]})
    payload: dict[str, Any] = {"content": content, "isError": result.get("status") == "error"}
    if result.get("structuredContent") is not None:
        payload["structuredContent"] = result["structuredContent"]
    return payload


def list_prompts() -> list[dict[str, Any]]:
    with _client() as client:
        return [
            {
                "name": p.name,
                "description": p.description or "",
                "arguments": [
                    {"name": a.name, "description": a.description or "", "required": bool(a.required)}
                    for a in (p.arguments or [])
                ],
            }
            for p in client.list_prompts_sync().prompts
        ]


def get_prompt_text(name: str, arguments: dict[str, str]) -> str:
    with _client() as client:
        result = client.get_prompt_sync(name, arguments)
    return "\n".join(m.content.text for m in result.messages if getattr(m.content, "text", None))


def list_tools() -> list[dict[str, Any]]:
    with _client() as client:
        return [
            {
                "name": t.mcp_tool.name,
                "description": t.mcp_tool.description or "",
                "input_schema": t.mcp_tool.input_schema or {},
                "ui_resource_uri": ui_resource_uri(t.mcp_tool),
            }
            for t in client.list_tools_sync()
        ]


def list_resources() -> list[dict[str, Any]]:
    with _client() as client:
        return [
            {
                "uri": str(r.uri),
                "name": r.name or "",
                "description": r.description or "",
                "mime_type": r.mime_type or "",
            }
            for r in client.list_resources_sync().resources
        ]


def ui_tools() -> dict[str, str]:
    """Map of tool name → `ui://` resource URI for every UI-bound tool (cached)."""
    global _ui_tools
    with _cache_lock:
        if _ui_tools and time.monotonic() - _ui_tools[0] < CACHE_TTL_SECONDS:
            return _ui_tools[1]
    with _client() as client:
        mapping = {
            t.mcp_tool.name: uri
            for t in client.list_tools_sync()
            if (uri := ui_resource_uri(t.mcp_tool))
        }
    with _cache_lock:
        _ui_tools = (time.monotonic(), mapping)
    return mapping


def read_ui_resource(uri: str) -> dict[str, Any] | None:
    """Fetch a widget's HTML and `_meta.ui` (csp, permissions…). None if unknown."""
    if uri not in set(ui_tools().values()):
        return None
    with _cache_lock:
        cached = _ui_resources.get(uri)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    with _client() as client:
        contents = client.read_resource_sync(uri).contents
    if not contents or not getattr(contents[0], "text", None):
        return None
    item = contents[0]
    ui = (item.meta or {}).get("ui") or {}
    payload = {
        "uri": uri,
        "mimeType": item.mime_type,
        "html": item.text,
        "csp": ui.get("csp") or {},
        "permissions": ui.get("permissions") or {},
        "prefersBorder": ui.get("prefersBorder"),
    }
    with _cache_lock:
        _ui_resources[uri] = (time.monotonic(), payload)
    return payload


def call_ui_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Call a UI-bound tool on behalf of a widget. None if the tool isn't UI-bound."""
    if name not in ui_tools():
        return None
    with _client() as client:
        result = client.call_tool_sync(f"widget-{int(time.time() * 1000)}", name, arguments)
    return to_call_tool_result(result)


def _reset_cache() -> None:
    """Testing hook."""
    global _ui_tools
    with _cache_lock:
        _ui_tools = None
        _ui_resources.clear()
