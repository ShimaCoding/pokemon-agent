"""Bill's box tools and prompt (backend/bill.py); the SQLite file is a temp one and the Pokédex lookup is faked."""

import asyncio
import json
import logging

import pytest

from backend import bill, box

ASH = "a5a5a5a5-0000-4000-8000-" + "a" * 12
MISTY = "b6b6b6b6-0000-4000-8000-" + "b" * 12

POKEDEX = {
    "pikachu": {"id": 25, "types": ["electric"], "defense": {"weak_to": {"ground": 2}, "resists": {"flying": 0.5}, "immune_to": []}},
    "mr-mime": {"id": 122, "types": ["psychic", "fairy"]},
    "giratina-altered": {"id": 487, "types": ["ghost", "dragon"]},
    "gengar": {"id": 94, "types": ["ghost", "poison"]},
    "staryu": {"id": 120, "types": ["water"]},
}


def entry_result(name: str) -> dict:
    """A get_pokedex_entry result as the MCP server returns it (the fields the box keeps, and more)."""
    data = POKEDEX[name]
    entry = {
        "name": name,
        "genus": "Pokémon de prueba",
        "base_stats": {"hp": 35, "attack": 55, "defense": 40, "special-attack": 50, "special-defense": 50, "speed": 90},
        "flavor_text": "…",
        **data,
    }
    return {"status": "success", "isError": False, "content": [{"text": json.dumps(entry, ensure_ascii=False)}]}


def not_found(name: str) -> dict:
    text = f"❌ Pokémon '{name}' not found. Please check the name or ID."
    return {"status": "error", "isError": True, "content": [{"text": text}]}


class Lookup:
    """Stands in for tool_cache.fetch over get_pokedex_entry; records what was asked."""

    def __init__(self, slugs: dict[str, str] | None = None) -> None:
        self.asked: list[str] = []
        self.slugs = {"Mr. Mime": "mr-mime", "giratina": "giratina-altered", **(slugs or {})}
        self.down: set[str] = set()

    async def __call__(self, name: str) -> dict:
        self.asked.append(name)
        slug = self.slugs.get(name, name)
        if slug in self.down:
            return {"status": "error", "isError": True, "content": [{"text": "❌ Error: PokeAPI no responde"}]}
        return entry_result(slug) if slug in POKEDEX else not_found(slug)


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "DB_PATH", str(tmp_path / "box.db"))


def tools(trainer_id: str | None = ASH, lookup: Lookup | None = None):
    return bill.box_tools(trainer_id, lookup or Lookup())


def run(coro) -> dict | str:
    text = asyncio.run(coro)
    try:
        return json.loads(text)
    except ValueError:
        return text


def test_the_three_box_tools_and_their_schemas():
    store, release, listing = tools()
    assert {t.tool_name for t in (store, release, listing)} == bill.BOX_TOOLS
    assert bill.NAME_TOOLS < bill.BOX_TOOLS
    for t in (store, release):
        schema = t.tool_spec["inputSchema"]["json"]
        assert schema["properties"]["pokemon"]["type"] == "array" and schema["required"] == ["pokemon"]
        # The trainer is never an argument the model could choose.
        assert set(schema["properties"]) == {"pokemon"}
    assert listing.tool_spec["inputSchema"]["json"].get("properties", {}) == {}


def test_store_keeps_only_real_pokemon():
    lookup = Lookup()
    store, _, listing = tools(lookup=lookup)
    result = run(store(pokemon=["pikachu", "pikachuzard", "Mr. Mime"]))
    assert result == {
        "stored": ["pikachu", "mr-mime"],
        "already": [],
        "not_found": ["pikachuzard"],
        "failed": [],
        "full": [],
        "count": 2,
        "capacity": box.MAX_POKEMON,
    }
    assert lookup.asked == ["pikachu", "pikachuzard", "Mr. Mime"]
    listed = run(listing())
    assert [p["name"] for p in listed["pokemon"]] == ["pikachu", "mr-mime"]
    # The box keeps a copy of the entry's static data, not the whole entry.
    assert listed["pokemon"][0] == {
        "name": "pikachu",
        "id": 25,
        "types": ["electric"],
        "base_stats": {"hp": 35, "attack": 55, "defense": 40, "special-attack": 50, "special-defense": 50, "speed": 90},
        "defense": {"weak_to": {"ground": 2}, "resists": {"flying": 0.5}, "immune_to": []},
    }


def test_store_reports_what_was_already_there_and_what_did_not_fit(monkeypatch):
    monkeypatch.setattr(box, "MAX_POKEMON", 2)
    store, _, _ = tools()
    run(store(pokemon=["pikachu"]))
    result = run(store(pokemon=["pikachu", "gengar", "staryu"]))
    assert (result["stored"], result["already"], result["full"]) == (["gengar"], ["pikachu"], ["staryu"])
    assert (result["count"], result["capacity"]) == (2, 2)


def test_a_failed_lookup_is_not_a_missing_pokemon_and_stores_nothing():
    lookup = Lookup()
    lookup.down = {"gengar"}
    store, _, listing = tools(lookup=lookup)
    result = run(store(pokemon=["gengar"]))
    assert (result["stored"], result["not_found"], result["failed"], result["count"]) == ([], [], ["gengar"], 0)
    assert run(listing())["count"] == 0

    async def broken(name):
        raise RuntimeError("mcp down")

    store, _, _ = bill.box_tools(ASH, broken)
    assert run(store(pokemon=["pikachu"]))["failed"] == ["pikachu"]


def test_a_success_that_is_not_a_full_entry_is_not_stored():
    async def partial(name):
        return {"status": "success", "content": [{"text": json.dumps({"name": name, "id": 25})}]}

    store, _, _ = bill.box_tools(ASH, partial)
    result = run(store(pokemon=["pikachu"]))
    assert (result["stored"], result["failed"]) == ([], ["pikachu"])


def test_release_matches_names_as_the_model_writes_them():
    store, release, listing = tools()
    run(store(pokemon=["pikachu", "Mr. Mime", "giratina"]))
    result = run(release(pokemon=["Pikachu", "mr mime", "Giratina", "mew"]))
    assert result == {
        "released": ["pikachu", "mr-mime", "giratina-altered"],
        "not_in_box": ["mew"],
        "count": 0,
        "capacity": box.MAX_POKEMON,
    }
    assert run(listing())["pokemon"] == []


@pytest.mark.parametrize(
    ("name", "stored", "match"),
    [
        ("pikachu", ["pikachu"], "pikachu"),
        ("Mr. Mime", ["mr-mime"], "mr-mime"),
        ("giratina", ["giratina-altered"], "giratina-altered"),
        ("giratina", ["giratina-altered", "giratina-origin"], None),  # never a guess between two forms
        ("giratina-origin", ["giratina-altered", "giratina-origin"], "giratina-origin"),
        ("raichu", ["pikachu"], None),
    ],
)
def test_match_in_box(name, stored, match):
    assert bill.match_in_box(name, stored) == match


@pytest.mark.parametrize("pokemon", [[], ["a", "b", "c", "d", "e", "f", "g"], [" "]])
def test_store_and_release_take_one_to_six_names(pokemon):
    lookup = Lookup()
    store, release, _ = tools(lookup=lookup)
    assert "error" in run(store(pokemon=pokemon))
    assert "error" in run(release(pokemon=pokemon))
    assert lookup.asked == []


def test_each_trainer_only_reaches_their_own_box():
    ash_store, ash_release, ash_list = tools(ASH)
    misty_store, _, misty_list = tools(MISTY)
    run(ash_store(pokemon=["pikachu"]))
    run(misty_store(pokemon=["staryu", "gengar"]))

    assert [p["name"] for p in run(ash_list())["pokemon"]] == ["pikachu"]
    assert run(ash_release(pokemon=["staryu"]))["not_in_box"] == ["staryu"]
    assert [p["name"] for p in run(misty_list())["pokemon"]] == ["staryu", "gengar"]


def test_without_a_trainer_the_box_is_unavailable():
    lookup = Lookup()
    store, release, listing = tools(None, lookup)
    assert run(store(pokemon=["pikachu"])) == bill.UNAVAILABLE
    assert run(release(pokemon=["pikachu"])) == bill.UNAVAILABLE
    assert run(listing()) == bill.UNAVAILABLE
    assert lookup.asked == []
    assert "recargar la página" in bill.UNAVAILABLE


def test_a_storage_failure_is_reported_without_details(monkeypatch, caplog):
    def broken(trainer_id, *args):
        raise RuntimeError(f"disk full for {trainer_id}")

    monkeypatch.setattr(box, "store", broken)
    monkeypatch.setattr(box, "list_box", broken)
    store, release, listing = tools()
    with caplog.at_level(logging.ERROR):
        assert run(store(pokemon=["pikachu"])) == bill.BOX_ERROR
        assert run(listing()) == bill.BOX_ERROR
        assert run(release(pokemon=["pikachu"])) == bill.BOX_ERROR
    # The log names the failure, never the trainer (not even through the exception's message).
    assert "RuntimeError" in caplog.text
    assert ASH not in caplog.text


def test_no_result_or_log_line_carries_the_trainer_id(caplog):
    store, release, listing = tools()
    with caplog.at_level(logging.DEBUG):
        results = [
            asyncio.run(store(pokemon=["pikachu", "pikachuzard"])),
            asyncio.run(listing()),
            asyncio.run(release(pokemon=["pikachu", "mew"])),
        ]
    assert ASH not in "".join(results)
    assert ASH not in caplog.text
    for t in (store, release, listing):
        assert ASH not in json.dumps(t.tool_spec)


def test_tools_run_through_the_generic_tool_stream():
    store, _, _ = tools()

    async def call():
        tool_use = {"toolUseId": "tu-1", "name": "box_store", "input": {"pokemon": ["pikachu"]}}
        return [e async for e in store.stream(tool_use, {})][-1].tool_result

    result = asyncio.run(call())
    assert result["status"] == "success"
    assert json.loads(result["content"][0]["text"])["stored"] == ["pikachu"]


def test_bill_prompt_is_dry_spanish_and_reports_the_occupancy():
    prompt = bill.BILL_PROMPT
    assert prompt.lstrip().startswith("Eres Bill")
    assert f'"N de {box.MAX_POKEMON}"' in prompt
    assert "sin personalidad" in prompt
    # One box only, and no "empty the box".
    assert "es la única que existe para ti" in prompt
    assert 'No existe "vaciar la caja"' in prompt
    for name in (*bill.BOX_TOOLS, *bill.MCP_TOOLS):
        assert name in prompt
    # Nothing of Dexter's persona, and no placeholder for a trainer id.
    assert "Dexter, otro agente" in prompt and "sarc" not in prompt
    assert "{" not in prompt and "trainer_id" not in prompt


def test_bill_models_prefer_the_small_model():
    assert bill.DEFAULT_MODELS == ["groq/openai/gpt-oss-20b", "groq/openai/gpt-oss-120b"]
