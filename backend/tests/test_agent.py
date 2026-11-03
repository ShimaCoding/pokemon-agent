"""
Tests for the harness configuration in backend/agent.py.

The harness ships tools that are dangerous on a public site (shell, file
editing, code execution, subagents, web fetch). The agent must expose exactly
the MCP server's tools — nothing that a future harness release adds may slip in
silently.

`retrieve_context` is gone on purpose (#18): it reads back what the context
manager offloads by reference, but our compaction never offloads (old tool
results become a "call the tool again" stub), so the stash is disabled and the
tool would only cost ~290 prompt tokens per call.

`skills` is gone on purpose (#16): loading the Dexter narration skill cost a
whole LLM round-trip per Pokédex question and re-sent ~900 tokens of
instructions in every later call, so the protocol now lives inline in
SYSTEM_PROMPT. The tests below also make sure no AgentSkills plugin comes
back through a harness default.
"""

import copy
import shutil
from pathlib import Path

import pytest
from strands._async import run_async
from strands.models import ModelRouter
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient
from strands.tools.tools import PythonAgentTool
from strands.vended_plugins.skills import AgentSkills

from backend import agent as agent_module
from backend.agent import (
    EXPECTED_TOOLS,
    HARNESS_TOOLS,
    MCP_TOOLS,
    SYSTEM_PROMPT,
    create_agent,
    pokemon_slug,
)
from backend.context import (
    INDEX_PREFIX,
    TOOL_RESULT_STUB,
    TURN_STATE_KEY,
    CompactPastTurns,
    next_turn,
    turn_starts,
)
from backend.providers import MODELS_AUTHENTICATED, MODELS_PUBLIC


def _fake_mcp_tool(name: str) -> PythonAgentTool:
    spec = {"name": name, "description": name, "inputSchema": {"json": {"type": "object", "properties": {}}}}
    return PythonAgentTool(name, spec, lambda tool_use, **_: {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []})


@pytest.fixture
def harness_env(monkeypatch, tmp_path):
    """Build agents without network: fake MCP tools, a dummy Groq key and a temp session dir."""
    captured = {}

    def fake_load_servers(config, **kwargs):
        captured["config"] = config
        captured["kwargs"] = kwargs
        return [_fake_mcp_tool(name) for name in sorted(MCP_TOOLS)]

    monkeypatch.setattr(MCPClient, "load_servers", staticmethod(fake_load_servers))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(agent_module, "SESSIONS_DIR", str(tmp_path))
    return captured


def test_agent_exposes_exactly_the_expected_tools(harness_env):
    agent = create_agent("test-conversation")
    assert set(agent.tool_names) == EXPECTED_TOOLS
    assert HARNESS_TOOLS == frozenset()
    assert EXPECTED_TOOLS == MCP_TOOLS
    assert "retrieve_context" not in agent.tool_names


def test_agent_has_no_dangerous_harness_tools(harness_env):
    agent = create_agent("test-conversation")
    dangerous = {"shell", "read", "write", "edit", "programmatic_tool_caller", "subagent", "web_fetch", "web_search", "todos"}
    assert not dangerous & set(agent.tool_names)


def test_mcp_server_is_unprefixed_and_fatal_on_error(harness_env):
    create_agent("test-conversation")
    server = harness_env["config"]["pokemon"]
    assert server["prefix"] == ""
    assert server["continue_on_error"] is False


def test_agent_uses_model_router(harness_env):
    public = create_agent("test-conversation")
    models = [c.model.get_config()["model_id"] for c in _router_candidates(public)]
    assert models == MODELS_PUBLIC

    auth = create_agent("test-conversation", authenticated=True)
    models = [c.model.get_config()["model_id"] for c in _router_candidates(auth)]
    assert models == MODELS_AUTHENTICATED


def _router_candidates(agent):
    routers = [p for p in agent._plugin_registry._plugins.values() if isinstance(p, ModelRouter)]
    assert len(routers) == 1
    return routers[0].candidates


def test_agent_has_no_skills_plugin_or_tool(harness_env):
    agent = create_agent("test-conversation")
    assert "skills" not in agent.tool_names
    assert not any(isinstance(p, AgentSkills) for p in agent._plugin_registry._plugins.values())


def test_system_prompt_inlines_narration_protocol(harness_env):
    # Routing: one tool brings everything the narration needs.
    assert "llama a get_pokedex_entry" in SYSTEM_PROMPT
    assert "no llames además get_pokemon_info" in SYSTEM_PROMPT
    # Opening rule (the #23 pokedex_opening check depends on it) and untitled closing.
    assert '"BULBASAUR. El Pokémon Semilla."' in SYSTEM_PROMPT
    assert "genus exacto" in SYSTEM_PROMPT
    assert "Cierre sin título ni encabezado" in SYSTEM_PROMPT
    assert "sin títulos de sección" in SYSTEM_PROMPT
    assert "omítelo en vez de inventarlo" in SYSTEM_PROMPT
    # Past tool results are stubbed and old turns only indexed (#18): re-call,
    # never answer or quote them from memory.
    assert INDEX_PREFIX.strip("[: ") in SYSTEM_PROMPT
    assert "vuelve a llamar a la herramienta según ese índice" in SYSTEM_PROMPT
    assert "nunca respondas ni cites datos de memoria" in SYSTEM_PROMPT
    # Persona stays.
    assert SYSTEM_PROMPT.lstrip().startswith("Eres Dexter")
    assert "disonancia léxica" in SYSTEM_PROMPT
    # Nothing asks for the removed skill.
    assert "activa el skill" not in SYSTEM_PROMPT
    assert "dexter-pokedex-narrator" not in SYSTEM_PROMPT

    agent = create_agent("test-conversation")
    assert agent.system_prompt == SYSTEM_PROMPT
    assert "<available_skills>" not in agent.system_prompt


def test_system_prompt_rejects_unknown_or_mismatched_entry():
    # "háblame de pikachuzard" must not become a Pikachu narration: the tool
    # gets the name the user wrote, and a not-found result or another species'
    # entry is reported as missing from the records (epic #15 measurements,
    # regression 1). A number or a form of the same species is not a mismatch.
    assert "el nombre que escribió el usuario, nunca otro parecido" in SYSTEM_PROMPT
    assert 'Si responde "not found" o trae otra especie (una forma o un número no cuentan), no lo narres' in SYSTEM_PROMPT
    assert "no existe en los registros" in SYSTEM_PROMPT


def test_system_prompt_reports_other_failures_as_scan_failures():
    # A timeout or MCP error is not "no existe": a real Pokémon must never be
    # declared missing because the lookup failed.
    assert "si falla por otro motivo, informa la falla del escaneo y sugiere reintentar" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("written", "slug"),
    [
        ("pikachu", "pikachu"),
        ("Mr. Mime", "mr-mime"),
        ("farfetch'd", "farfetchd"),
        ("Farfetch’d", "farfetchd"),
        ("tapu koko", "tapu-koko"),
        ("Nidoran♀", "nidoran-f"),
        ("Flabébé", "flabebe"),
        ("Type: Null", "type-null"),
        ("Ho-Oh", "ho-oh"),
        ("25", "25"),
        ("Giratina", "giratina-altered"),
        ("mimikyu", "mimikyu-disguised"),
        ("giratina-origin", "giratina-origin"),
        # Format only: an unknown name is never swapped for a known one.
        ("pikachuzard", "pikachuzard"),
    ],
)
def test_pokemon_slug(written, slug):
    assert pokemon_slug(written) == slug


def test_tool_calls_get_pokeapi_slugs(harness_env):
    from strands.hooks import BeforeToolCallEvent

    agent = create_agent("test-conversation")

    def call(name, tool_input):
        tool_use = {"toolUseId": "t1", "name": name, "input": tool_input}
        agent.hooks.invoke_callbacks(BeforeToolCallEvent(agent=agent, selected_tool=None, tool_use=tool_use, invocation_state={}))
        return tool_use["input"]

    assert call("get_pokedex_entry", {"pokemon": "Mr. Mime"}) == {"pokemon": "mr-mime"}
    assert call("analyze_pokemon_stats", {"name_or_id": "Giratina"}) == {"name_or_id": "giratina-altered"}
    assert call("compare_pokemon", {"pokemon_names": ["Tapu Koko", "pikachu"]}) == {"pokemon_names": ["tapu-koko", "pikachu"]}
    # Other arguments and non-MCP tools are left alone.
    assert call("get_type_effectiveness", {"attacking_type": "Fire"}) == {"attacking_type": "Fire"}
    assert call("retrieve_context", {"pokemon": "Mr. Mime"}) == {"pokemon": "Mr. Mime"}


def test_system_prompt_budget():
    # The prompt is sent on every LLM call (cached after the first). The inline
    # protocol must stay condensed (~360 tokens, #16): the persona above it
    # already covers the tone rules of the old SKILL.md. The unknown-name rules
    # (epic #15 regression 1) take it to ~1,820 characters.
    protocol = SYSTEM_PROMPT[SYSTEM_PROMPT.index("Protocolo de narración") :]
    # +45 for the genus_lang rule of #47 (PokeAPI has no Spanish genus for Gen IX).
    assert len(protocol) <= 1870
    # +250 over #16 for the past-results and index rule of #18 (~70 tokens per call).
    # +350 for get_evolution_chain routing and narration (#47, ~90 tokens per call).
    assert len(SYSTEM_PROMPT) <= 4100


def test_restored_session_gets_current_system_prompt(harness_env):
    # Snapshots persist the system prompt; an old conversation must still run
    # with the current one, while keeping its messages.
    agent = create_agent("conv-stale-prompt")
    agent.system_prompt = "OLD PROMPT"
    agent.messages.append({"role": "user", "content": [{"text": "háblame de bulbasaur"}]})
    run_async(lambda: agent._session_manager.save_snapshot(agent, is_latest=True))
    agent.cleanup()

    restored = create_agent("conv-stale-prompt")
    assert restored.system_prompt == SYSTEM_PROMPT
    assert restored.messages == [{"role": "user", "content": [{"text": "háblame de bulbasaur"}]}]


def test_agent_uses_compacting_context_manager(harness_env, tmp_path):
    agent = create_agent("test-conversation")
    manager = agent.context_manager
    assert manager.stash is None
    # Ours first; the SDK's overflow guard stays last.
    assert [s.name for s in manager._strategies] == [CompactPastTurns.name, "offload:emergency-truncate"]
    agent.messages.append({"role": "user", "content": [{"text": "hola"}]})
    run_async(lambda: agent._session_manager.save_snapshot(agent, is_latest=True))
    assert not (tmp_path / "context").exists()


SESSION6 = Path(__file__).parent / "fixtures" / "session6_snapshot.json"


def test_restores_legacy_snapshot_and_compacts_it(harness_env, monkeypatch, tmp_path):
    # A pre-#18 snapshot: 6 turns, full tool results, an external stash
    # reference and no turn counter. It must restore as is and get compacted
    # before the first model call, and the compacted form must be persisted.
    conversation_id = "legacy-session6"
    snapshot = tmp_path / "session" / conversation_id / "scopes" / "agent" / "default" / "snapshots" / "snapshot_latest.json"
    snapshot.parent.mkdir(parents=True)
    shutil.copy(SESSION6, snapshot)

    agent = create_agent(conversation_id)
    assert len(agent.messages) == 34
    assert agent.context_manager.stash is None
    assert next_turn(agent) == 7

    sent: list[list[dict]] = []

    async def fake_stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        sent.append(copy.deepcopy(messages))
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "MEWTWO. El Pokémon Genético."}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}

    monkeypatch.setattr(LiteLLMModel, "stream", fake_stream)
    agent("¿y la de mewtwo?")
    agent.cleanup()

    # What the model saw: 4 past turns (squirtle onwards), headed by the index
    # of the dropped ones, + the new question.
    (messages,) = sent
    assert len(turn_starts(messages)) == 5
    index, question = messages[0]["content"]
    assert question == {"text": "ahora squirtle"}
    assert "1) bulbasaur (get_pokedex_entry); 2) charmander (get_pokedex_entry)" in index["text"]
    results = [b["toolResult"] for m in messages for b in m["content"] if "toolResult" in b]
    assert results and all(r["content"] == [{"text": TOOL_RESULT_STUB}] for r in results)
    assert "pikachu" in str(messages) and "eevee" in str(messages)

    # Persisted: the snapshot holds exactly that history plus the reply.
    restored = create_agent(conversation_id)
    assert [(m["role"], m["content"]) for m in restored.messages[:-1]] == [(m["role"], m["content"]) for m in messages]
    assert restored.messages[-1]["content"] == [{"text": "MEWTWO. El Pokémon Genético."}]
    assert not any("reasoningContent" in b for m in restored.messages for b in m["content"])
    assert restored.state.get(TURN_STATE_KEY) == 7


def test_prompt_routes_evolution_questions_to_get_evolution_chain():
    from backend.agent import POKEMON_NAME_ARGS, SYSTEM_PROMPT

    assert "get_evolution_chain" in MCP_TOOLS
    assert "pokemon" in POKEMON_NAME_ARGS  # get_evolution_chain(pokemon) is slugged
    assert "get_evolution_chain" in SYSTEM_PROMPT
    assert "nunca des etapas ni niveles de memoria" in SYSTEM_PROMPT
