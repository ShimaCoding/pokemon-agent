"""
Tests for the process-level get_pokedex_entry cache (backend/tool_cache.py).

Unit tests drive the middleware with a fake ExecuteToolContext; the integration
tests run it through the real create_agent + strands tool executor + _ToolTrace,
with a fake MCP tool instead of the network.
"""

import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
from strands.middleware import ToolResultEvent
from strands.tools.mcp import MCPClient
from strands.tools.tools import PythonAgentTool

from backend import main, tool_cache
from backend.agent import MCP_TOOLS, create_agent
from backend.tests.test_agent import _fake_mcp_tool, harness_env  # noqa: F401  (fixture)

CARD_URI = "ui://pokedex/card.html"


def _entry(name: str = "pikachu", **overrides) -> dict:
    entry = {
        "id": 25,
        "name": name,
        "genus": "Pokémon Ratón",
        "types": ["electric"],
        "evolution_chain": [
            {"name": "pichu", "is_baby": True, "id": 172, "types": ["electric"], "total_bst": 205},
            {"name": "pikachu", "id": 25, "types": ["electric"], "total_bst": 320},
            {"name": "raichu", "id": 26, "types": ["electric"], "total_bst": 485},
        ],
    }
    entry.update(overrides)
    return entry


def _ok(tool_use_id: str, entry: dict | None = None) -> dict:
    """A strands MCP tool result, shaped like MCPClient._handle_tool_result's."""
    entry = entry or _entry()
    return {
        "status": "success",
        "toolUseId": tool_use_id,
        "content": [{"text": json.dumps(entry, ensure_ascii=False)}],
        "structuredContent": {"id": entry["id"], "name": entry["name"], "artwork": "https://img/25.png"},
        "metadata": {"io.modelcontextprotocol/serverInfo": {"name": "Pokemon MCP Server", "version": "0.1.0"}},
        "isError": False,
    }


def _err(tool_use_id: str) -> dict:
    return {
        "status": "error",
        "toolUseId": tool_use_id,
        "content": [{"text": "❌ Error: PokeAPI no responde"}],
        "isError": True,
    }


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.delenv("TOOL_CACHE_ENABLED", raising=False)
    tool_cache._reset_cache()
    yield
    tool_cache._reset_cache()


# --- Middleware unit tests ------------------------------------------------------


class _Live:
    """Fake next_fn: the live tool call, returning ``make(toolUseId)``."""

    def __init__(self, make=_ok):
        self.make = make
        self.calls = 0

    async def __call__(self, ctx):
        self.calls += 1
        yield {"progress": self.calls}  # a non-result event, must flow through
        yield ToolResultEvent(self.make(ctx.tool_use["toolUseId"]))


_uid = iter(range(1_000_000))


def _ctx(name="get_pokedex_entry", tool_input=None, tool=object(), state=None):
    tool_input = {"pokemon": "pikachu"} if tool_input is None else tool_input
    tool_use = {"toolUseId": f"tu-{next(_uid)}", "name": name, "input": tool_input}
    return SimpleNamespace(tool_use=tool_use, tool=tool, invocation_state={} if state is None else state)


def _run(ctx, live):
    """Run the middleware; return (final ToolResultEvent, non-result events)."""

    async def collect():
        return [e async for e in tool_cache.middleware(ctx, live)]

    events = asyncio.run(collect())
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(results) == 1 and events[-1] is results[0]
    return results[0], [e for e in events if not isinstance(e, ToolResultEvent)]


def _outcome(ctx):
    return tool_cache.status(ctx.invocation_state, ctx.tool_use["toolUseId"])


def test_miss_then_hit_calls_live_once():
    live = _Live()
    first_ctx, second_ctx = _ctx(), _ctx()
    first, extra = _run(first_ctx, live)
    second, extra2 = _run(second_ctx, live)

    assert live.calls == 1
    assert extra == [{"progress": 1}] and extra2 == []
    assert _outcome(first_ctx) == "miss" and _outcome(second_ctx) == "hit"
    assert second.tool_result["toolUseId"] == second_ctx.tool_use["toolUseId"]
    strip = lambda r: {k: v for k, v in r.items() if k != "toolUseId"}  # noqa: E731
    assert strip(second.tool_result) == strip(first.tool_result) == strip(_ok("x"))


def test_hit_returns_independent_copy():
    live = _Live()
    _run(_ctx(), live)
    hit, _ = _run(_ctx(), live)
    hit.tool_result["content"][0]["text"] = "mutated"
    hit.tool_result["structuredContent"]["name"] = "mutated"

    again, _ = _run(_ctx(), live)
    assert json.loads(again.tool_result["content"][0]["text"])["name"] == "pikachu"
    assert again.tool_result["structuredContent"]["name"] == "pikachu"
    assert live.calls == 1


def test_key_normalization():
    live = _Live()
    _run(_ctx(tool_input={"pokemon": " Pikachu "}), live)
    _run(_ctx(tool_input={"pokemon": "pikachu"}), live)
    assert live.calls == 1
    _run(_ctx(tool_input={"pokemon": "bulbasaur"}), live)
    assert live.calls == 2


def _bad_results():
    no_chain = _ok("x", _entry(evolution_chain=None))
    stage_without_id = _ok("x", _entry(evolution_chain=[{"name": "pichu"}, {"name": "pikachu", "id": 25}]))
    not_json = {"status": "success", "toolUseId": "x", "content": [{"text": "❌ Error: algo"}]}
    is_error = dict(_ok("x"), isError=True)
    return {
        "status_error": _err("x"),
        "is_error": is_error,
        "not_json": not_json,
        "no_evolution_chain": no_chain,
        "stage_without_id": stage_without_id,
    }


@pytest.mark.parametrize("kind", list(_bad_results()))
def test_incomplete_or_failed_results_are_not_cached(kind):
    bad = _bad_results()[kind]
    live = _Live(lambda uid: dict(copy.deepcopy(bad), toolUseId=uid))
    for _ in range(2):
        ctx = _ctx()
        result, _ = _run(ctx, live)
        assert result.tool_result["content"] == bad["content"]  # live result passed through
        assert _outcome(ctx) == "bypass"
    assert live.calls == 2


@pytest.mark.parametrize(
    "ctx",
    [
        _ctx(name="get_pokemon_info"),
        _ctx(name="skills", tool_input={"skill_name": "x"}),
        _ctx(tool=None),  # unknown tool: the terminal produces the error
    ],
)
def test_other_tools_pass_through(ctx):
    live = _Live()
    _run(ctx, live)
    _run(ctx, live)
    assert live.calls == 2
    assert tool_cache.STATE_KEY not in ctx.invocation_state


def _expire_entries(monkeypatch):
    """Every stored entry is past its TTL from now on."""
    monkeypatch.setattr(tool_cache, "TTL_SECONDS", 0)


def test_ttl_expiry_refreshes(monkeypatch):
    ttl = tool_cache.TTL_SECONDS
    live = _Live()
    _run(_ctx(), live)
    _expire_entries(monkeypatch)
    ctx = _ctx()
    _run(ctx, live)
    assert live.calls == 2 and _outcome(ctx) == "miss"
    monkeypatch.setattr(tool_cache, "TTL_SECONDS", ttl)
    _run(_ctx(), live)  # refreshed entry is fresh again
    assert live.calls == 2


def test_fresh_entry_is_served_when_the_api_is_down():
    _run(_ctx(), _Live())
    down = _Live(_err)
    ctx = _ctx()
    result, _ = _run(ctx, down)
    assert down.calls == 0
    assert result.tool_result["status"] == "success"
    assert _outcome(ctx) == "hit"


def test_stale_entry_is_served_when_the_live_call_fails(monkeypatch):
    _run(_ctx(), _Live())
    _expire_entries(monkeypatch)
    down = _Live(_err)
    ctx = _ctx()
    result, _ = _run(ctx, down)
    assert down.calls == 1
    assert _outcome(ctx) == "stale"
    assert result.tool_result["status"] == "success"
    assert result.tool_result["toolUseId"] == ctx.tool_use["toolUseId"]
    assert result.tool_result["structuredContent"] == _ok("x")["structuredContent"]


def test_lru_eviction(monkeypatch):
    monkeypatch.setattr(tool_cache, "MAX_ENTRIES", 2)
    live = _Live()
    for name in ("a", "b"):
        _run(_ctx(tool_input={"pokemon": name}), live)
    _run(_ctx(tool_input={"pokemon": "a"}), live)  # touch "a": "b" is now the oldest
    _run(_ctx(tool_input={"pokemon": "c"}), live)  # evicts "b"
    assert live.calls == 3

    _run(_ctx(tool_input={"pokemon": "a"}), live)
    assert live.calls == 3
    _run(_ctx(tool_input={"pokemon": "b"}), live)
    assert live.calls == 4


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("TOOL_CACHE_ENABLED", "0")
    live = _Live()
    ctx = _ctx()
    _run(ctx, live)
    _run(_ctx(), live)
    assert live.calls == 2
    assert _outcome(ctx) is None


def test_live_exception_is_passed_through():
    boom = ConnectionError("MCP down")

    async def live(ctx):
        yield ToolResultEvent(_err(ctx.tool_use["toolUseId"]), exception=boom)

    result, _ = _run(_ctx(), live)
    assert result.exception is boom


# --- Through the real agent ------------------------------------------------------


class _FakePokedex(PythonAgentTool):
    """get_pokedex_entry as the agent sees it: a tool with the MCP tool's `_meta.ui`."""

    def __init__(self):
        self.calls = 0
        self.mode = "ok"
        spec = {
            "name": "get_pokedex_entry",
            "description": "Pokédex entry",
            "inputSchema": {"json": {"type": "object", "properties": {"pokemon": {"type": "string"}}}},
        }
        super().__init__("get_pokedex_entry", spec, self._call)
        self.mcp_tool = SimpleNamespace(meta={"ui": {"resourceUri": CARD_URI}})

    def _call(self, tool_use, **_):
        self.calls += 1
        time.sleep(0.05)  # an MCP round trip, so a hit is visibly different
        if self.mode == "raise":
            raise ConnectionError("MCP server unreachable")
        if self.mode == "error":
            return _err(tool_use["toolUseId"])
        return _ok(tool_use["toolUseId"])


class _Timing:
    """Hook-to-hook time and executor duration of each tool call."""

    def __init__(self):
        self.started: dict[str, float] = {}
        self.calls: list[tuple[float, float]] = []

    def register_hooks(self, registry, **_):
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _before(self, event):
        self.started[event.tool_use["toolUseId"]] = time.perf_counter()

    def _after(self, event):
        hook_to_hook = time.perf_counter() - self.started[event.tool_use["toolUseId"]]
        self.calls.append((hook_to_hook * 1000, event.duration * 1000))


@pytest.fixture
def pokedex(harness_env, monkeypatch):  # noqa: F811
    tool = _FakePokedex()
    others = [_fake_mcp_tool(name) for name in sorted(MCP_TOOLS - {"get_pokedex_entry"})]
    monkeypatch.setattr(MCPClient, "load_servers", staticmethod(lambda config, **kw: [tool, *others]))
    return tool


def _ask(pokemon="pikachu", hooks=None):
    """One request: a fresh agent (as /api/agent/run builds) calling get_pokedex_entry."""
    events: list[dict] = []
    agent = create_agent("test-conversation", hooks=[main._ToolTrace(events.append), *(hooks or [])])
    result = agent.tool.get_pokedex_entry(pokemon=pokemon, record_direct_tool_call=False)
    return result, events


def test_agent_serves_pokedex_from_cache_across_requests(pokedex):
    first, _ = _ask()
    second, _ = _ask("Pikachu")
    assert pokedex.calls == 1
    assert second["status"] == "success"
    assert second["content"] == first["content"]


def test_cache_hit_is_under_5ms(pokedex):
    timing = _Timing()
    _ask(hooks=[timing])
    for _ in range(5):
        _ask(hooks=[timing])
    assert pokedex.calls == 1

    miss, *hits = timing.calls
    assert miss[1] >= 50  # the fake MCP round trip
    assert min(h for h, _ in hits) < 5, hits
    assert min(d for _, d in hits) < 5, hits


def test_cache_hit_emits_identical_widget_payload(pokedex):
    _, miss_events = _ask()
    _, hit_events = _ask()
    by_type = lambda events: {e["type"]: e for e in events}  # noqa: E731
    miss, hit = by_type(miss_events), by_type(hit_events)

    assert [e["type"] for e in hit_events] == ["tool_call", "tool_result", "ui_resource"]
    assert miss["tool_result"]["cached"] is False and hit["tool_result"]["cached"] is True
    assert hit["tool_result"]["result"] == miss["tool_result"]["result"]
    assert hit["tool_result"]["duration_ms"] < miss["tool_result"]["duration_ms"]

    assert hit["ui_resource"]["resource_uri"] == CARD_URI
    assert hit["ui_resource"]["tool_result"] == miss["ui_resource"]["tool_result"]
    assert hit["ui_resource"]["tool_result"]["structuredContent"] == _ok("x")["structuredContent"]
    assert hit["ui_resource"]["tool_result"]["isError"] is False


@pytest.mark.parametrize("mode", ["error", "raise"])
def test_cached_entry_survives_mcp_or_pokeapi_failure(pokedex, monkeypatch, mode):
    _ask()
    pokedex.mode = mode

    # Fresh entry: the failing server is not even called.
    result, events = _ask()
    assert pokedex.calls == 1
    assert result["status"] == "success"
    assert "ui_resource" in [e["type"] for e in events]

    # Expired entry: the live call fails, the stale copy is served instead.
    _expire_entries(monkeypatch)
    result, events = _ask()
    assert pokedex.calls == 2
    assert result["status"] == "success"
    assert {e["type"]: e for e in events}["tool_result"]["cached"] is True

    # Not cached: the failure reaches the agent as usual.
    result, _ = _ask("bulbasaur")
    assert result["status"] == "error"


# --- get_evolution_chain (#47) -----------------------------------------------------------


def _chain_result(root: dict) -> dict:
    return {"status": "success", "toolUseId": "x", "content": [{"text": json.dumps({"queried": "arboliva", "stages": 3, "root": root, "evolutions": []})}]}


def _stage(name: str, id_: int | None, *children: dict) -> dict:
    node = {"name": name, "evolves_to": list(children)}
    if id_ is not None:
        node["id"] = id_
    return node


def test_evolution_chain_is_cacheable_only_when_every_stage_has_an_id():
    full = _stage("smoliv", 928, _stage("dolliv", 929, _stage("arboliva", 930)))
    assert tool_cache.is_cacheable("get_evolution_chain", _chain_result(full))
    assert tool_cache.is_cacheable("get_evolution_chain", _chain_result(_stage("tauros", 128)))
    hole = _stage("smoliv", 928, _stage("dolliv", 929, _stage("arboliva", None)))
    assert not tool_cache.is_cacheable("get_evolution_chain", _chain_result(hole))
    not_found = {"status": "error", "toolUseId": "x", "content": [{"text": "❌ Pokémon 'x' not found."}]}
    assert not tool_cache.is_cacheable("get_evolution_chain", not_found)
    assert "get_evolution_chain" in tool_cache.CACHEABLE_TOOLS
