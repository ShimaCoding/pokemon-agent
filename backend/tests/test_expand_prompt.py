"""The slash menu's "Usa el prompt X con a=b …" → MCP prompt arguments (#91).

MCP is faked: ``list_prompts`` returns the six prompts and arguments the production
server declares in ``prompts/list``, and ``get_prompt_text`` records what it was sent.
"""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import main, mcp_gateway

# prompts/list of mcp.mcpokedex.com (names and required flags as the server declares them).
SERVER_PROMPTS = {
    "educational/pokemon-analysis": [("pokemon_name", True), ("analysis_type", False), ("user_level", False)],
    "educational/team-building": [("theme", False), ("format", False), ("restrictions", False)],
    "educational/type-effectiveness": [("scenario", False), ("attacking_type", False), ("defending_types", False)],
    "battle/strategy": [
        ("user_team", True),
        ("opponent_team", False),
        ("battle_format", False),
        ("strategy_focus", False),
    ],
    "battle/matchup-analysis": [("pokemon1", True), ("pokemon2", True), ("scenario", False), ("environment", False)],
    "battle/team-preview": [("team", True), ("analysis_depth", False), ("focus_areas", False)],
}

USE_PROMPTS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "hooks" / "usePrompts.ts"


def _server_prompts() -> list[dict]:
    return [
        {
            "name": name,
            "description": "",
            "arguments": [{"name": a, "description": "", "required": r} for a, r in args],
        }
        for name, args in SERVER_PROMPTS.items()
    ]


@pytest.fixture
def sent(monkeypatch):
    """Fake MCP; returns the list of (prompt, arguments) that reached ``get_prompt_text``."""
    calls: list[tuple[str, dict]] = []

    def get_prompt_text(name, arguments):
        calls.append((name, dict(arguments)))
        return f"texto de {name}"

    monkeypatch.setattr(mcp_gateway, "list_prompts", _server_prompts)
    monkeypatch.setattr(mcp_gateway, "get_prompt_text", get_prompt_text)
    return calls


def _expand(query: str) -> str:
    return asyncio.run(main._expand_prompt(query))


def _args_sent(sent, query: str) -> dict:
    _expand(query)
    assert len(sent) == 1, sent
    return sent[0][1]


# --- the parser ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args_str, expected",
    [
        ("user_team=pikachu,charizard,bulbasaur", {"user_team": "pikachu,charizard,bulbasaur"}),
        ("user_team=pikachu, charizard, bulbasaur", {"user_team": "pikachu,charizard,bulbasaur"}),
        (
            "team=gengar,mewtwo,dragonite,lapras,snorlax,alakazam",
            {"team": "gengar,mewtwo,dragonite,lapras,snorlax,alakazam"},
        ),
        (
            "team=gengar , mewtwo ,dragonite,  lapras,snorlax , alakazam",
            {"team": "gengar,mewtwo,dragonite,lapras,snorlax,alakazam"},
        ),
        ("restrictions=sin legendarios format=casual", {"restrictions": "sin legendarios", "format": "casual"}),
        ("pokemon1=pikachu pokemon2=charizard", {"pokemon1": "pikachu", "pokemon2": "charizard"}),
        ("pokemon1=pikachu, pokemon2=charizard", {"pokemon1": "pikachu", "pokemon2": "charizard"}),
        ("theme=balanced,format=casual", {"theme": "balanced", "format": "casual"}),
        ("theme = balanced  format= casual", {"theme": "balanced", "format": "casual"}),
        ("Pokemon_Name=Mr. Mime", {"pokemon_name": "Mr. Mime"}),
        ("restrictions= format=casual", {"format": "casual"}),
        ("theme=balanced restrictions=", {"theme": "balanced"}),
        ("team=gengar,", {"team": "gengar"}),
        ("", {}),
        ("sin pares", {}),
    ],
)
def test_parse_prompt_args(args_str, expected):
    assert main._parse_prompt_args(args_str) == expected


# --- _expand_prompt ---------------------------------------------------------------------


@pytest.mark.parametrize("sep", [",", ", "])
def test_a_team_reaches_the_server_whole(sent, sep):
    team = sep.join(["pikachu", "charizard", "bulbasaur", "gengar", "lapras", "snorlax"])
    args = _args_sent(sent, f"Usa el prompt battle/strategy con user_team={team} battle_format=singles")
    assert args == {"user_team": "pikachu,charizard,bulbasaur,gengar,lapras,snorlax", "battle_format": "singles"}


def test_keys_with_digits_reach_the_server(sent):
    args = _args_sent(sent, "Usa el prompt battle/matchup-analysis con pokemon1=pikachu pokemon2=charizard")
    assert args == {"pokemon1": "pikachu", "pokemon2": "charizard"}


def test_undeclared_keys_and_empty_values_are_dropped(sent):
    args = _args_sent(
        sent, "Usa el prompt educational/team-building con theme=balanced nivel=experto restrictions="
    )
    assert args == {"theme": "balanced"}


def test_expanded_text_wraps_the_prompt(sent):
    out = _expand("Usa el prompt battle/team-preview con team=gengar,mewtwo")
    assert out.startswith("Using MCP prompt 'battle/team-preview':\n\ntexto de battle/team-preview")


def _slash_menu_template(name: str, arguments: list[tuple[str, bool]], defaults: dict[str, str]) -> str:
    """``buildPromptTemplate`` of usePrompts.ts."""
    parts = [f"{a}={defaults.get(a, f'[{a}]')}" for a, _ in arguments if defaults.get(a) != ""]
    return f"Usa el prompt {name}" + (f" con {' '.join(parts)}" if parts else "")


def _slash_menu_defaults() -> dict[str, str]:
    source = USE_PROMPTS_TS.read_text()
    block = source.split("PROMPT_ARG_DEFAULTS", 1)[1].split("}", 1)[0]
    defaults = dict(re.findall(r"^\s*(\w+):\s*'([^']*)',?\s*$", block, re.M))
    assert "user_team" in defaults and "restrictions" in defaults  # the parse found the block
    return defaults


@pytest.mark.parametrize("name", list(SERVER_PROMPTS))
def test_slash_menu_templates_reach_the_server_complete(sent, name):
    defaults = _slash_menu_defaults()
    arguments = SERVER_PROMPTS[name]
    args = _args_sent(sent, _slash_menu_template(name, arguments, defaults))
    assert sent[0][0] == name
    assert args == {a: defaults[a] for a, _ in arguments if defaults.get(a)}


def test_text_that_is_not_a_prompt_is_left_alone(monkeypatch):
    monkeypatch.setattr(mcp_gateway, "list_prompts", lambda: pytest.fail("must not reach MCP"))
    monkeypatch.setattr(mcp_gateway, "get_prompt_text", lambda *a: pytest.fail("must not reach MCP"))
    query = "Compara a Arcanine, Gyarados y Dragonite con team=pikachu"
    assert _expand(query) == query


def test_unknown_prompt_leaves_the_query(sent):
    query = "Usa el prompt battle/nope con team=pikachu"
    assert _expand(query) == query
    assert sent == []


def test_list_prompts_error_leaves_the_query(monkeypatch):
    def boom():
        raise RuntimeError("MCP down")

    monkeypatch.setattr(mcp_gateway, "list_prompts", boom)
    monkeypatch.setattr(mcp_gateway, "get_prompt_text", lambda *a: pytest.fail("must not get the prompt"))
    query = "Usa el prompt battle/team-preview con team=pikachu"
    assert _expand(query) == query


def test_get_prompt_error_leaves_the_query(monkeypatch):
    def boom(name, arguments):
        raise RuntimeError("Error rendering prompt battle/team-preview.")

    monkeypatch.setattr(mcp_gateway, "list_prompts", _server_prompts)
    monkeypatch.setattr(mcp_gateway, "get_prompt_text", boom)
    query = "Usa el prompt battle/team-preview con team=pikachu,gengar"
    assert _expand(query) == query


# --- mcp_gateway --------------------------------------------------------------------------


class _FakeClient:
    connections = 0

    def __init__(self, fail: bool = False):
        self.fail = fail

    def __enter__(self):
        _FakeClient.connections += 1
        if self.fail:
            raise RuntimeError("MCP down")
        return self

    def __exit__(self, *exc):
        return False

    def list_prompts_sync(self):
        arg = SimpleNamespace(name="team", description=None, required=True)
        prompt = SimpleNamespace(name="battle/team-preview", description=None, arguments=[arg])
        return SimpleNamespace(prompts=[prompt])


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.connections = 0
    mcp_gateway._reset_cache()
    yield monkeypatch
    mcp_gateway._reset_cache()


def test_list_prompts_is_cached(fake_client):
    fake_client.setattr(mcp_gateway, "_client", _FakeClient)
    first = mcp_gateway.list_prompts()
    assert mcp_gateway.list_prompts() == first
    assert mcp_gateway.prompt_argument_names("battle/team-preview") == frozenset({"team"})
    assert mcp_gateway.prompt_argument_names("battle/nope") is None
    assert _FakeClient.connections == 1


def test_a_failed_prompt_list_is_not_cached(fake_client):
    fake_client.setattr(mcp_gateway, "_client", lambda: _FakeClient(fail=True))
    with pytest.raises(RuntimeError):
        mcp_gateway.list_prompts()
    fake_client.setattr(mcp_gateway, "_client", _FakeClient)
    assert mcp_gateway.list_prompts()[0]["name"] == "battle/team-preview"
    assert _FakeClient.connections == 2
