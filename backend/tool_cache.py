"""
Process-level cache for read-only MCP tool results (issue #20).

Pokédex data does not change, yet every ``get_pokedex_entry`` call costs an MCP
round trip (165-450 ms in prod) and depends on PokeAPI being up. The agent is
rebuilt per request, so the cache lives at module level and is plugged into
each agent as ``ExecuteToolStage`` middleware (``create_agent``).

A hit short-circuits the tool call: no MCP server, no PokeAPI, and the same
strands result dict the live call returned (``content``, ``structuredContent``,
``metadata``), so the LLM reads the same text and the widget gets the same
``CallToolResult``. The widget URI comes from the tool listing, not the result,
so ``_ToolTrace`` still emits ``ui_resource`` on hits.

- Only complete, successful results are stored (see ``is_cacheable``).
- Fresh for ``TTL_SECONDS``; LRU-bounded to ``MAX_ENTRIES``.
- Stale-if-error: past the TTL the tool is called again, and if that call fails
  the expired copy is served instead.
- ``TOOL_CACHE_ENABLED=0`` turns it into a pass-through.

Each call's outcome (``hit``/``miss``/``stale``/``bypass``) is written to
``invocation_state[STATE_KEY][toolUseId]`` for the tool trace.
"""

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, AsyncIterator

from strands.middleware import ExecuteToolContext, ToolResultEvent

logger = logging.getLogger(__name__)

CACHEABLE_TOOLS = frozenset({"get_pokedex_entry", "get_evolution_chain"})
STATE_KEY = "mcpokedex:tool_cache"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        logger.warning("[CACHE] %s inválido, usando %d", name, default)
        return default


TTL_SECONDS = _env_int("TOOL_CACHE_TTL_SECONDS", 86_400)
MAX_ENTRIES = _env_int("TOOL_CACHE_MAX_ENTRIES", 512)

_lock = threading.Lock()
# (tool, normalized input) -> (stored_at, result JSON without toolUseId). Stored as
# text so a hit is always a fresh copy: later in-place edits can't poison the cache.
_entries: "OrderedDict[tuple[str, str], tuple[float, str]]" = OrderedDict()


def enabled() -> bool:
    return os.environ.get("TOOL_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def cache_key(name: str, tool_input: Any) -> tuple[str, str] | None:
    """Key for a call, or None if the input can't be keyed. Strings are trimmed and lowercased."""
    if not isinstance(tool_input, dict):
        return None
    normalized = {k: v.strip().lower() if isinstance(v, str) else v for k, v in tool_input.items()}
    try:
        return name, json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def is_cacheable(name: str, result: dict[str, Any]) -> bool:
    """Only complete, successful results are worth keeping for a day.

    The MCP server fills ``evolution_chain`` best-effort: if PokeAPI hiccups there,
    it still returns success with a missing chain or stages without ``id``.
    """
    if result.get("status") != "success" or result.get("isError"):
        return False
    try:
        entry = json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    if not isinstance(entry, dict):
        return False
    if name == "get_evolution_chain":
        return isinstance(entry.get("root"), dict) and _complete_tree(entry["root"])
    if "id" not in entry:
        return False
    if name == "get_pokedex_entry":
        chain = entry.get("evolution_chain")
        return isinstance(chain, list) and bool(chain) and all(isinstance(s, dict) and "id" in s for s in chain)
    return True


def _complete_tree(node: Any) -> bool:
    """Every stage of an evolution tree has its ``id`` (PokeAPI answered for all of them)."""
    if not isinstance(node, dict) or "id" not in node:
        return False
    children = node.get("evolves_to", [])
    return isinstance(children, list) and all(_complete_tree(c) for c in children)


def _get(key: tuple[str, str]) -> tuple[dict[str, Any], bool] | None:
    """(result copy, still fresh) for a key, or None."""
    with _lock:
        item = _entries.get(key)
        if item is None:
            return None
        _entries.move_to_end(key)
    stored_at, raw = item
    return json.loads(raw), time.monotonic() - stored_at < TTL_SECONDS


def _put(key: tuple[str, str], result: dict[str, Any]) -> bool:
    payload = {k: v for k, v in result.items() if k != "toolUseId"}
    try:
        raw = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    with _lock:
        _entries[key] = (time.monotonic(), raw)
        _entries.move_to_end(key)
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)
    return True


def _mark(ctx: ExecuteToolContext, outcome: str) -> None:
    ctx.invocation_state.setdefault(STATE_KEY, {})[ctx.tool_use.get("toolUseId", "")] = outcome


def status(invocation_state: dict[str, Any] | None, tool_use_id: str) -> str | None:
    """Cache outcome of a tool call (``hit``/``miss``/``stale``/``bypass``), None if not cacheable."""
    return ((invocation_state or {}).get(STATE_KEY) or {}).get(tool_use_id)


async def middleware(ctx: ExecuteToolContext, next_fn) -> AsyncIterator[Any]:
    """ExecuteToolStage Wrap handler. Exceptions (incl. interrupts) are never swallowed."""
    name = ctx.tool_use.get("name", "")
    key = None
    if name in CACHEABLE_TOOLS and ctx.tool is not None and enabled():
        key = cache_key(name, ctx.tool_use.get("input"))
    if key is None:
        async for event in next_fn(ctx):
            yield event
        return

    cached = _get(key)
    if cached is not None and cached[1]:
        result = cached[0]
        result["toolUseId"] = ctx.tool_use["toolUseId"]
        _mark(ctx, "hit")
        logger.info("[CACHE] hit %s %s", name, key[1])
        yield ToolResultEvent(result)
        return

    # Miss (or expired): call live, holding the result back so it can be swapped.
    final: ToolResultEvent | None = None
    async for event in next_fn(ctx):
        if isinstance(event, ToolResultEvent):
            final = event
            continue
        yield event
    if final is None:
        return  # the executor reports the missing result

    if is_cacheable(name, final.tool_result):
        _mark(ctx, "miss" if _put(key, final.tool_result) else "bypass")
    elif cached is not None:
        result = cached[0]
        result["toolUseId"] = ctx.tool_use["toolUseId"]
        _mark(ctx, "stale")
        logger.warning("[CACHE] stale %s %s (falló la llamada en vivo)", name, key[1])
        final = ToolResultEvent(result)
    else:
        _mark(ctx, "bypass")
    yield final


def _reset_cache() -> None:
    """Testing hook."""
    with _lock:
        _entries.clear()
