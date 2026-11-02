"""
Tests for the harness configuration in backend/agent.py.

The harness ships tools that are dangerous on a public site (shell, file
editing, code execution, subagents, web fetch). The agent must expose exactly
the MCP server's tools plus `skills` and `retrieve_context` — nothing that a
future harness release adds may slip in silently.
"""

import pytest
from strands._async import run_async
from strands.models import ModelRouter
from strands.tools.mcp import MCPClient
from strands.tools.tools import PythonAgentTool

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
