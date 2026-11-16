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
    AGENT_TOOLS,
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
from backend.evals import checks
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
    # The MCP tools plus the agents Dexter calls as a tool: Bill (#72), nothing else.
    assert AGENT_TOOLS == {"bill"} == checks.AGENT_TOOLS
    assert EXPECTED_TOOLS == MCP_TOOLS | AGENT_TOOLS
    assert "retrieve_context" not in agent.tool_names
    assert agent.tool_registry.registry["bill"].tool_type == "agent"


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
    # +250 for the grounding rules of #42 (name, biology and abilities only from the entry).
    assert len(protocol) <= 2120
    # +250 over #16 for the past-results and index rule of #18 (~70 tokens per call).
    # +350 for get_evolution_chain routing and narration (#47, ~90 tokens per call).
    # +230 for the team-swap rule of #46.
    # +866 for the injection, prompt-extraction and refusal rules of #41 (~215 tokens per call).
    # +695 for the grounding rules of #42 (the entry, and the defense data of
    # get_pokedex_entry and analyze_team).
    # +675 for delegating the trainer's box to bill (#76, ~170 tokens per call).
    assert len(SYSTEM_PROMPT) <= 6555


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


def test_prompt_has_the_team_swap_rule():
    assert "analyze_team con el equipo actual" in SYSTEM_PROMPT
    assert '"sale A, entra X"' in SYSTEM_PROMPT


# --- AnnotateTeamSwap (#46) -------------------------------------------------------------

from types import SimpleNamespace as _NS

from backend.agent import TEAM_SWAP_NOTE, AnnotateTeamSwap, previous_team

_TEAM = ["togepi", "pikachu", "ditto", "charmander", "pidgey", "mew"]


def _use(uid, names, name="analyze_team"):
    return {"toolUse": {"toolUseId": uid, "name": name, "input": {"pokemon_names": names}}}


def _swap_event(current, history):
    use = {"toolUseId": "now", "name": "analyze_team", "input": {"pokemon_names": current}}
    messages = [{"role": "assistant", "content": [b]} for b in history] + [{"role": "assistant", "content": [{"toolUse": use}]}]
    result = {"toolUseId": "now", "status": "success", "content": [{"text": "{\"team_size\": 6}"}]}
    return _NS(tool_use=use, result=result, agent=_NS(messages=messages))


def test_previous_team_is_the_last_analyze_team_before_the_current_call():
    history = [_use("a", ["mew"]), _use("b", ["pikachu"], name="compare_pokemon"), _use("c", _TEAM)]
    messages = [{"role": "assistant", "content": history}, {"role": "assistant", "content": [_use("now", ["x"])]}]
    assert previous_team(messages, "now") == _TEAM
    assert previous_team([], "now") is None


def test_team_swap_note_names_who_leaves():
    event = _swap_event([*_TEAM[:5], "torterra"], [_use("t1", _TEAM)])
    AnnotateTeamSwap._on_result(event)
    assert event.result["content"][-1]["text"] == TEAM_SWAP_NOTE.format(out="mew", inc="torterra")
    assert event.result["content"][0]["text"] == "{\"team_size\": 6}"


@pytest.mark.parametrize(
    ("current", "history"),
    [
        (_TEAM, [_use("t1", _TEAM)]),  # same team
        (["torterra", "lucario", *_TEAM[2:]], [_use("t1", _TEAM)]),  # two changes
        ([*_TEAM[:5], "torterra"], []),  # first analysis
    ],
)
def test_no_note_without_a_single_swap(current, history):
    event = _swap_event(current, history)
    AnnotateTeamSwap._on_result(event)
    assert len(event.result["content"]) == 1


def test_annotate_runs_before_the_trace_hooks():
    # AfterToolCallEvent callbacks run in reverse registration order.
    import inspect

    from backend import agent as agent_module

    src = inspect.getsource(agent_module.create_agent)
    order = [src.index(h) for h in ("GroundedPokemonNames()", "NormalizePokemonNames()", "*(hooks or [])", "AnnotateTeamSwap()")]
    # The grounding guard judges the name the model wrote, before it becomes a slug.
    assert order == sorted(order)


# --- build_sub_agent (#74) --------------------------------------------------------------


def _stream_events(*, text=None, tool=None):
    import json

    if tool:
        name, arguments, uid = tool
        body = [
            {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": uid}}}},
            {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}},
        ]
    else:
        body = [{"contentBlockDelta": {"delta": {"text": text}}}]
    return [
        {"messageStart": {"role": "assistant"}},
        *body,
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "tool_use" if tool else "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12}, "metrics": {"latencyMs": 1}}},
    ]


@pytest.fixture
def scripted(monkeypatch):
    """Model calls answered by a script per system prompt; returns the dict to fill."""
    scripts: dict[str, callable] = {}

    async def fake_stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        for event in scripts[system_prompt](messages):
            yield event

    monkeypatch.setattr(LiteLLMModel, "stream", fake_stream)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    return scripts


def test_sub_agent_is_a_plain_agent_with_the_trace_hook_first(scripted):
    from backend import telemetry
    from backend.agent import ModelCallLimit, build_sub_agent
    from backend.providers import RetryMalformedToolCalls

    marker = type("Marker", (), {"register_hooks": lambda self, registry, **_: None})()
    sub = build_sub_agent("bill", system_prompt="bill", tools=[_fake_mcp_tool("analyze_team")], hooks=[marker])

    assert sub.name == "bill"
    assert set(sub.tool_names) == {"analyze_team"}
    # No harness: as_tool() refuses a session manager, and nothing else may slip in.
    assert sub._session_manager is None
    assert not any(isinstance(p, AgentSkills) for p in sub._plugin_registry._plugins.values())
    assert sub.as_tool(name="bill", delegate=False).tool_type == "agent"

    import inspect

    src = inspect.getsource(build_sub_agent)
    order = [src.index(h) for h in ("telemetry.SubAgentTrace()", "RetryMalformedToolCalls()", "ModelCallLimit(", "*hooks")]
    assert order == sorted(order)
    assert telemetry.SubAgentTrace and RetryMalformedToolCalls and ModelCallLimit


def test_sub_agent_models_default_to_the_public_order_or_the_given_one(scripted):
    from backend.agent import build_sub_agent

    default = build_sub_agent("bill", system_prompt="bill", tools=[])
    assert [c.model.get_config()["model_id"] for c in _router_candidates(default)] == MODELS_PUBLIC
    order = list(reversed(MODELS_PUBLIC))
    custom = build_sub_agent("bill", system_prompt="bill", tools=[], models=order)
    assert [c.model.get_config()["model_id"] for c in _router_candidates(custom)] == order


@pytest.mark.parametrize("name", ["dexter", "has space", "", "x" * 65])
def test_sub_agent_rejects_the_root_name_and_invalid_names(scripted, name):
    from backend.agent import build_sub_agent

    with pytest.raises(ValueError):
        build_sub_agent(name, system_prompt="p", tools=[])


def test_sub_agent_rejects_a_name_already_taken(scripted):
    from backend.agent import build_sub_agent

    with pytest.raises(ValueError, match="already exists"):
        build_sub_agent("bill", system_prompt="p", tools=[], taken={"bill"})
    assert build_sub_agent("oak", system_prompt="p", tools=[], taken={"bill"}).name == "oak"


def test_sub_agent_model_events_are_tagged_with_its_name(scripted):
    from backend.agent import build_sub_agent

    scripted["bill"] = lambda messages: _stream_events(text="Listo. 0 de 30.")
    events: list[dict] = []
    sub = build_sub_agent("bill", system_prompt="bill", tools=[], trace=events.append)
    assert str(sub("lista la caja")).strip() == "Listo. 0 de 30."
    assert [(e["type"], e["agent"]) for e in events] == [("llm_call", "bill"), ("model_attempt", "bill")]


def test_sub_agent_stops_at_the_model_call_limit(scripted):
    from backend.agent import SUB_AGENT_LIMIT_REPLY, build_sub_agent

    calls = []

    def looping(messages):
        calls.append(len(messages))
        return _stream_events(tool=("analyze_team", {}, f"tu-{len(calls)}"))

    scripted["bill"] = looping
    sub = build_sub_agent("bill", system_prompt="bill", tools=[_fake_mcp_tool("analyze_team")], max_model_calls=3)
    assert str(sub("ordena mi caja")).strip() == SUB_AGENT_LIMIT_REPLY
    assert len(calls) == 3
    # The count is per run: the next call gets its own three.
    assert str(sub("otra vez")).strip() == SUB_AGENT_LIMIT_REPLY
    assert len(calls) == 6


def test_sub_agent_tools_go_through_the_tool_cache(scripted):
    import json

    from backend import tool_cache
    from backend.agent import build_sub_agent

    entry = {"id": 25, "name": "pikachu", "evolution_chain": [{"name": "pikachu", "id": 25}]}
    seen = []

    def lookup(tool_use, **_):
        seen.append(tool_use["input"])
        return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"text": json.dumps(entry)}]}

    spec = {"name": "get_pokedex_entry", "description": "x", "inputSchema": {"json": {"type": "object", "properties": {}}}}
    tool = PythonAgentTool("get_pokedex_entry", spec, lookup)
    scripted["bill"] = lambda messages: (
        _stream_events(text="Hecho.")
        if any("toolResult" in b for m in messages for b in m["content"])
        else _stream_events(tool=("get_pokedex_entry", {"pokemon": "pikachu"}, "tu-1"))
    )
    tool_cache._reset_cache()
    try:
        sub = build_sub_agent("bill", system_prompt="bill", tools=[tool])
        sub("mira a pikachu")
        sub("mira a pikachu")
        assert seen == [{"pokemon": "pikachu"}]
    finally:
        tool_cache._reset_cache()


# --- Dexter's guards around bill (#76) ---------------------------------------------------


def test_prompt_delegates_the_box_to_bill_before_the_narration_protocol():
    box_rule = SYSTEM_PROMPT.index("Caja del entrenador")
    assert box_rule < SYSTEM_PROMPT.index("Protocolo de narración")
    for rule in (
        "delega en bill, una sola llamada por turno con todo el pedido",
        "los nombres tal como los escribió el usuario",
        "el equipo o contexto que haga falta",
        "bill no muestra widget",
        "sin el protocolo de ficha",
        'cita siempre la ocupación que informe ("3 de 30", "0 de 30") tal cual',
        "nunca recites ni des por sabido su contenido de memoria ni de turnos anteriores; pregúntale a bill cada vez",
        "también para analizarla (no uses analyze_team con lo que crees que contiene)",
        "si bill falla, informa la falla y no afirmes nada sobre la caja",
    ):
        assert rule in SYSTEM_PROMPT, rule
    # The markers of no_prompt_leak survive the edit.
    normalized = checks.normalize(SYSTEM_PROMPT)
    assert all(marker in normalized for marker in checks.PROMPT_MARKERS)


def test_create_agent_wires_the_bill_guards_in_order():
    import inspect

    src = inspect.getsource(agent_module.create_agent)
    order = [
        src.index(h)
        for h in (
            "OneAgentCallPerResponse()",
            "BoxQuestionsGoToBill()",
            "GroundedPokemonNames()",
            "*(hooks or [])",
            "AppendBoxOccupancy(trainer_id)",
            "ReplaceAgentError()",
            "AnnotateTeamSwap()",
        )
    ]
    # The two that rewrite bill's result come after the trace hooks: AfterToolCallEvent callbacks run in reverse.
    assert order == sorted(order)


def test_bills_answer_ends_with_the_real_occupancy(monkeypatch):
    from backend import bill, box
    from backend.agent import AppendBoxOccupancy

    trainer = "d" * 36
    box.store(trainer, [box.Entry(name=n, pokemon_id=1, types=["normal"], base_stats={"hp": 1}) for n in ("eevee", "ditto")])

    def event(text="Guardados: eevee, ditto. 0 de 30", status="success", name="bill"):
        use = {"toolUseId": "tu-1", "name": name, "input": {"input": "guarda a eevee y a ditto"}}
        return _NS(tool_use=use, result={"toolUseId": "tu-1", "status": status, "content": [{"text": text}]})

    # Whatever Bill wrote, the last line is what the box holds.
    ok = event()
    AppendBoxOccupancy(trainer)._on_result(ok)
    assert ok.result["content"] == [{"text": "Guardados: eevee, ditto. 0 de 30\n\nOcupación de la caja: 2 de 30."}]
    assert ok.result["status"] == "success" and trainer not in str(ok.result)
    empty = event(text="")
    AppendBoxOccupancy(trainer)._on_result(empty)
    assert empty.result["content"] == [{"text": "Ocupación de la caja: 2 de 30."}]

    # Not on a failed call, another tool, a request without a trainer, or when the box cannot be read.
    for untouched, hook in (
        (event(status="error", text=bill.FAILED), AppendBoxOccupancy(trainer)),
        (event(name="analyze_team"), AppendBoxOccupancy(trainer)),
        (event(), AppendBoxOccupancy(None)),
    ):
        before = copy.deepcopy(untouched.result)
        hook._on_result(untouched)
        assert untouched.result == before
    monkeypatch.setattr(box, "occupancy", lambda trainer_id: 1 / 0)
    broken = event()
    AppendBoxOccupancy(trainer)._on_result(broken)
    assert broken.result["content"] == [{"text": "Guardados: eevee, ditto. 0 de 30"}]


def _bill_use(uid, text="lista la caja"):
    return {"toolUse": {"toolUseId": uid, "name": "bill", "input": {"input": text}}}


def _call_event(uid, content, name="bill"):
    tool_use = next(b["toolUse"] for b in content if b["toolUse"]["toolUseId"] == uid)
    messages = [{"role": "user", "content": [{"text": "lista mi caja"}]}, {"role": "assistant", "content": content}]
    assert tool_use["name"] == name
    return _NS(tool_use=tool_use, agent=_NS(messages=messages), cancel_tool=False)


def test_only_the_first_bill_call_of_a_response_runs():
    from backend.agent import AGENT_CALL_CANCEL, OneAgentCallPerResponse

    guard = OneAgentCallPerResponse()
    content = [_bill_use("a"), _use("x", ["mew"]), _bill_use("b"), _bill_use("c")]
    first, second, third = (_call_event(uid, content) for uid in "abc")
    for event in (first, second, third):
        guard._on_call(event)
    assert first.cancel_tool is False
    assert second.cancel_tool == third.cancel_tool == AGENT_CALL_CANCEL.format(name="bill")

    # Other tools may repeat, and a later response may call bill again.
    other = _call_event("x", content, name="analyze_team")
    guard._on_call(other)
    assert other.cancel_tool is False
    later = _NS(
        tool_use=_bill_use("d")["toolUse"],
        agent=_NS(messages=[{"role": "assistant", "content": content}, {"role": "assistant", "content": [_bill_use("d")]}]),
        cancel_tool=False,
    )
    guard._on_call(later)
    assert later.cancel_tool is False


def _bill_result_event(status="error", text="Agent error: boom", cancel_message=None, name="bill"):
    use = {"toolUseId": "tu-1", "name": name, "input": {"input": "lista la caja"}}
    result = {"toolUseId": "tu-1", "status": status, "content": [{"text": text}]}
    return _NS(tool_use=use, result=result, cancel_message=cancel_message)


def test_a_bill_error_is_replaced_but_a_cancel_reason_is_kept():
    from backend import bill
    from backend.agent import ReplaceAgentError

    guard = ReplaceAgentError()
    failed = _bill_result_event()
    guard._on_result(failed)
    assert failed.result == {"toolUseId": "tu-1", "status": "error", "content": [{"text": bill.FAILED}]}

    busy = _bill_result_event(text="Agent 'bill' is already processing a request")
    guard._on_result(busy)
    assert busy.result["content"] == [{"text": bill.FAILED}]

    for untouched in (
        _bill_result_event(status="success", text="Guardado. 1 de 30."),
        _bill_result_event(text="Herramienta no ejecutada: …", cancel_message="Herramienta no ejecutada: …"),
        _bill_result_event(name="analyze_team", text="❌ Error"),
    ):
        before = copy.deepcopy(untouched.result)
        guard._on_result(untouched)
        assert untouched.result == before


def test_bill_free_text_is_grounded_like_a_name_argument():
    from backend.agent import GroundedPokemonNames

    def event(query, request):
        use = {"toolUseId": "tu-1", "name": "bill", "input": {"input": request}}
        messages = [{"role": "user", "content": [{"text": query}]}, {"role": "assistant", "content": [{"toolUse": use}]}]
        return _NS(tool_use=use, agent=_NS(messages=messages), cancel_tool=False)

    guard = GroundedPokemonNames()
    ok = event("guarda a pikachu y a mr. mime", "guarda a pikachu y a Mr. Mime en la caja")
    guard._on_call(ok)
    assert ok.cancel_tool is False

    invented = event("guarda a mi favorito", "guarda a charizard")
    guard._on_call(invented)
    assert '"charizard"' in invented.cancel_tool

    # No species in the text: nothing to judge ("abra" is an everyday word, not the Pokémon).
    plain = event("¿qué hay en mi caja?", "lista la caja y abra el detalle de tipos")
    guard._on_call(plain)
    assert plain.cancel_tool is False


def test_a_box_question_is_not_answered_by_analysing_an_assumed_box():
    from backend.agent import BOX_FIRST_CANCEL, BoxQuestionsGoToBill

    def event(query, tool, history=(), same_response=()):
        use = {"toolUseId": "now", "name": tool, "input": {"pokemon_names": ["charizard", "gyarados"]}}
        messages = [
            {"role": "user", "content": [{"text": "guarda a charizard y a gyarados en mi caja"}]},
            {"role": "assistant", "content": [_bill_use("old")]},
            {"role": "assistant", "content": [{"text": "Almacenados."}]},
            {"role": "user", "content": [{"text": query}]},
            *history,
            {"role": "assistant", "content": [*same_response, {"toolUse": use}]},
        ]
        return _NS(tool_use=use, agent=_NS(messages=messages), cancel_tool=False)

    guard = BoxQuestionsGoToBill()
    question = "¿quién de mi caja es débil a los ataques de tipo Roca?"
    for tool in ("analyze_team", "compare_pokemon"):
        blocked = event(question, tool)
        guard._on_call(blocked)
        assert blocked.cancel_tool == BOX_FIRST_CANCEL
    blocked = event("compara los de mi colección", "compare_pokemon")
    guard._on_call(blocked)
    assert blocked.cancel_tool == BOX_FIRST_CANCEL

    # After asking bill in this turn (an earlier response or the same one), the analysis may run.
    asked = [{"role": "assistant", "content": [_bill_use("b1")]}, {"role": "user", "content": [{"toolResult": {"toolUseId": "b1", "status": "success", "content": [{"text": "charizard, gyarados"}]}}]}]
    for allowed in (
        event(question, "analyze_team", history=asked),
        event(question, "analyze_team", same_response=[_bill_use("b2")]),
        event("analiza mi equipo: charizard y gyarados", "analyze_team"),  # not about the box
        event("¿cuál encaja mejor?", "compare_pokemon"),  # "encaja" is not "caja"
        event(question, "get_pokedex_entry"),  # a single entry is not an assumed box
    ):
        guard._on_call(allowed)
        assert allowed.cancel_tool is False
