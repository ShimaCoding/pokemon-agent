"""
Tests for the harness configuration in backend/agent.py.

The harness ships tools that are dangerous on a public site (shell, file
editing, code execution, subagents, web fetch). The agent must expose exactly
the MCP server's tools plus `retrieve_context` — nothing that a future harness
release adds may slip in silently.

`skills` is gone on purpose (#16): loading the Dexter narration skill cost a
whole LLM round-trip per Pokédex question and re-sent ~900 tokens of
instructions in every later call, so the protocol now lives inline in
SYSTEM_PROMPT. The tests below also make sure no AgentSkills plugin comes
back through a harness default.
"""

import pytest
from strands._async import run_async
from strands.models import ModelRouter
from strands.tools.mcp import MCPClient
from strands.tools.tools import PythonAgentTool
from strands.vended_plugins.skills import AgentSkills

from backend import agent as agent_module
from backend.agent import EXPECTED_TOOLS, MCP_TOOLS, SYSTEM_PROMPT, create_agent
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
    # "háblame de pikachuzard" must not become a Pikachu narration: the name
    # goes to the tool verbatim, and an error or another Pokémon's entry is
    # reported as missing from the records (epic #15 measurements, regression 1).
    assert "tal como lo escribió el usuario, sin corregirlo ni adivinarlo" in SYSTEM_PROMPT
    assert "la herramienta falla o devuelve la ficha de otro Pokémon, no la narres" in SYSTEM_PROMPT
    assert "no existe en los registros" in SYSTEM_PROMPT


def test_system_prompt_budget():
    # The prompt is sent on every LLM call (cached after the first). The inline
    # protocol must stay condensed (~360 tokens, #16): the persona above it
    # already covers the tone rules of the old SKILL.md.
    protocol = SYSTEM_PROMPT[SYSTEM_PROMPT.index("Protocolo de narración") :]
    assert len(protocol) <= 1800
    assert len(SYSTEM_PROMPT) <= 3500


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
