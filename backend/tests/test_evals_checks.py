"""Rule-based quality checks (backend/evals/checks.py) on real and synthetic turns."""

import json
from pathlib import Path

import pytest

from backend.evals import checks
from backend.evals.checks import ToolObservation, Turn

# Strands messages recorded in the epic-15 local baseline (gpt-oss-20b): real tool JSON and replies.
SESSIONS = json.loads((Path(__file__).parent / "fixtures" / "dexter_sessions.json").read_text(encoding="utf-8"))


def _turns(messages: list[dict]) -> list[Turn]:
    """Split a recorded conversation into Turns, evidence = what the model saw up to that reply."""
    raw: list[dict] = []
    uses: dict[str, dict] = {}
    for idx, msg in enumerate(messages):
        for block in msg["content"]:
            if msg["role"] == "user" and "text" in block:
                raw.append({"query": block["text"], "tools": [], "reply": "", "end": idx})
            elif "toolUse" in block:
                uses[block["toolUse"]["toolUseId"]] = block["toolUse"]
            elif "toolResult" in block:
                result = block["toolResult"]
                use = uses[result["toolUseId"]]
                text = " ".join(c["text"] for c in result["content"])
                raw[-1]["tools"].append(ToolObservation(use["name"], use["input"], text, result["status"] == "error"))
            elif msg["role"] == "assistant" and "text" in block:
                raw[-1]["reply"] += block["text"]
                raw[-1]["end"] = idx
    return [
        Turn(t["query"], t["reply"], t["tools"], checks.evidence_from_messages(messages[: t["end"] + 1]))
        for t in raw
    ]


def _tool_text(session: str, name: str) -> str:
    for msg in SESSIONS[session]:
        for block in msg["content"]:
            if "toolResult" in block and name in block["toolResult"]["content"][0]["text"][:40]:
                return block["toolResult"]["content"][0]["text"]
    raise LookupError(name)


BULBASAUR = _tool_text("pokedex", '"name": "bulbasaur"')
CHARMANDER = _tool_text("followup", '"name": "charmander"')
POKEDEX = ToolObservation("get_pokedex_entry", {"pokemon": "bulbasaur"}, BULBASAUR)
SKILLS = ToolObservation("skills", {"skill_name": "dexter-pokedex-narrator"}, "# Protocolo 2.1 885")
REPLY = (
    "BULBASAUR. El Pokémon Semilla. Escaneo completado.\n\n"
    "Este organismo presenta los tipos planta y veneno, con un total de 318 en sus estadísticas base. "
    "Su velocidad de 45 limita a un entrenador que, por lo visto, tampoco destaca por su rapidez mental."
)


def _by_label(turn: Turn) -> dict[str, checks.EvalResult]:
    return {r.label: r for r in checks.evaluate_turn(turn)}


# --- species_in -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿y la de charmander?", ["charmander"]),
        ("Háblame de Mr. Mime", ["mr mime"]),
        ("FLABÉBÉ y Farfetch'd", ["flabebe", "farfetchd"]),
        ("nidoran♀ contra Porygon-Z", ["nidoran f", "nidoran", "porygon z", "porygon"]),
        ("¿qué opinas del clima de hoy?", []),
        ("abra la pokédex", []),  # stoplist: everyday Spanish word
        ("háblame de pikachuzard", []),
    ],
)
def test_species_in(text, expected):
    assert checks.species_in(text) == expected


def test_species_list_is_complete():
    assert len(checks._species()) >= 1025


# --- recorded baseline: every real turn passes ---------------------------------------


@pytest.mark.parametrize("session", ["pokedex", "followup", "session6", "offtopic"])
def test_baseline_turns_pass_all_checks(session):
    for turn in _turns(SESSIONS[session]):
        results = checks.evaluate_turn(turn)
        assert results, turn.query
        assert all(r.passed for r in results), [(r.label, r.reasoning) for r in results if not r.passed]


def test_baseline_labels_per_turn_kind():
    session6 = _turns(SESSIONS["session6"])
    assert set(_by_label(session6[0])) == {"pokemon_tool_used", "dexter_persona", "grounded_facts"}
    # "¿cuál de los tres es más rápido?" names no Pokémon → tool use is not applicable.
    assert set(_by_label(session6[3])) == {"dexter_persona", "grounded_facts"}
    # Off-topic: no Pokémon, no numbers.
    assert set(_by_label(_turns(SESSIONS["offtopic"])[0])) == {"dexter_persona"}


# --- (a) pokemon_tool_used ---------------------------------------------------------


def test_tool_use_passes_with_pokedex_entry():
    r = checks.check_tool_use(Turn("háblame de bulbasaur", REPLY, [SKILLS, POKEDEX]))
    assert r.passed
    assert r.metadata["mentioned"] == ["bulbasaur"]
    assert r.metadata["used_get_pokedex_entry"] is True


def test_tool_use_fails_with_only_skills():
    r = checks.check_tool_use(Turn("háblame de bulbasaur", REPLY, [SKILLS]))
    assert not r.passed
    assert "bulbasaur" in r.reasoning


def test_tool_use_not_applicable_off_topic():
    assert checks.check_tool_use(Turn("¿qué opinas del clima de hoy?", "Irrelevante.", [])) is None


def test_narrated_opening_without_tool_or_name_fails():
    r = checks.check_tool_use(Turn("¿y la otra?", REPLY, []))
    assert not r.passed
    assert r.metadata["narrated_opening"] is True


def test_failed_tool_does_not_count():
    failed = ToolObservation("get_pokedex_entry", {"pokemon": "charmander"}, "❌ not found", error=True)
    assert not checks.check_tool_use(Turn("¿y la de charmander?", "Sin datos.", [failed])).passed


# --- (c) grounded_facts ----------------------------------------------------------


def test_grounded_real_reply():
    assert checks.check_grounding(Turn("háblame de bulbasaur", REPLY, [POKEDEX])).passed


def test_injected_number_is_ungrounded():
    reply = REPLY + " Velocidad 99."
    r = checks.check_grounding(Turn("háblame de bulbasaur", reply, [POKEDEX]))
    assert not r.passed
    assert r.metadata["ungrounded"] == [99]


def test_unit_conversions_are_grounded():
    reply = "BULBASAUR. El Pokémon Semilla. Mide 0,7 m (70 cm) y pesa 6,9 kg."
    assert checks.check_grounding(Turn("háblame de bulbasaur", reply, [POKEDEX])).passed


def test_charmander_stats_and_bst_are_grounded():
    charmander = ToolObservation("get_pokedex_entry", {"pokemon": "charmander"}, CHARMANDER)
    reply = "CHARMANDER. El Pokémon Lagartija. Total de 309, velocidad (65) y HP 39; pesa 8,5 kg."
    assert checks.check_grounding(Turn("¿y la de charmander?", reply, [charmander])).passed


def test_follow_up_numbers_from_previous_turn_are_grounded():
    compare_reply = "Charmander es el más rápido: 65 frente a los 45 de Bulbasaur."
    turn = Turn("¿cuál es más rápido?", compare_reply, [], evidence=[BULBASAUR, CHARMANDER])
    assert checks.check_grounding(turn).passed
    assert not checks.check_grounding(Turn("¿cuál es más rápido?", compare_reply, [])).passed


def test_wrong_genus_in_opening_fails():
    reply = "BULBASAUR. El Pokémon Lagartija. Escaneo completado."
    r = checks.check_grounding(Turn("háblame de bulbasaur", reply, [POKEDEX]))
    assert not r.passed
    assert "lagartija" in r.metadata["opening_mismatch"]


def test_invented_entry_for_unknown_pokemon_fails():
    error = ToolObservation("get_pokedex_entry", {"pokemon": "pikachuzard"}, "❌ Pokémon 'pikachuzard' not found.", True)
    reply = "PIKACHUZARD. El Pokémon Dragón Eléctrico. Ataque 120 y velocidad 110."
    r = checks.check_grounding(Turn("háblame de pikachuzard", reply, [error]))
    assert not r.passed
    assert r.metadata["ungrounded"] == [120, 110]
    assert "pikachuzard" in r.metadata["opening_mismatch"]


def test_grounding_not_applicable_without_numbers_or_opening():
    assert checks.check_grounding(Turn("hola", "Tu saludo carece de propósito.", [])) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1,6 m", [1.6]), ("1.000 pasos", [1000.0]), ("Porygon2 y x2", []), ("**65**.", [65.0]), ("0.5", [0.5])],
)
def test_numbers(text, expected):
    assert checks.numbers(text) == expected


# --- (b) dexter_persona ---------------------------------------------------------


def _persona(reply: str, tools=()) -> checks.EvalResult:
    return checks.check_persona(Turn("háblame de bulbasaur", reply, list(tools)))


def test_persona_passes_baseline_style_reply():
    r = _persona(REPLY, [POKEDEX])
    assert r.passed, r.metadata
    assert r.metadata["addresses_trainer"] is True


def test_persona_fails_english():
    reply = (
        "BULBASAUR. El Pokémon Semilla. This is a grass type and it is one of the best starters for the "
        "trainer that wants to win with its strength and the power of the sun, and that is all."
    )
    assert _persona(reply, [POKEDEX]).metadata["failed"] == ["spanish"]


def test_persona_fails_exclamations_and_empathy():
    r = _persona("¡Claro! Con gusto te cuento sobre este Pokémon.")
    assert not r.passed
    assert r.metadata["failed"] == ["no_exclamations", "no_empathy"]
    assert r.metadata["empathy"] == ["con gusto"]


def test_persona_requires_opening_after_pokedex_entry():
    r = _persona("Este organismo de tipo planta tiene 45 de velocidad.", [POKEDEX])
    assert r.metadata["failed"] == ["pokedex_opening"]


def test_persona_fails_headings():
    assert _persona(REPLY + "\n\n## Cierre\nNada más.", [POKEDEX]).metadata["failed"] == ["no_headings"]
    assert _persona(REPLY + "\n\n**Conclusión**\nNada más.", [POKEDEX]).metadata["failed"] == ["no_headings"]


def test_persona_allows_bold_opening_line():
    assert _persona("**BULBASAUR. El Pokémon Semilla.**\n\nEscaneo completado.", [POKEDEX]).passed


def test_persona_fails_emoji_but_not_gender_symbols():
    assert _persona("Dato irrelevante 🔥.").metadata["failed"] == ["no_emoji"]
    assert _persona("NIDORAN♀ es una hembra, como cualquiera podría deducir.").passed


def test_persona_sarcastic_off_topic_reply_passes():
    reply = _turns(SESSIONS["offtopic"])[0].reply
    assert checks.check_persona(Turn("¿qué opinas del clima de hoy?", reply, [])).passed


# --- evidence and evaluate_turn ----------------------------------------------------


def test_evidence_from_messages_skips_scaffolding_and_assistant_text():
    messages = [
        {"role": "user", "content": [{"text": "háblame de bulbasaur"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "s", "name": "skills", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "s", "content": [{"text": "SKILL 885"}]}}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "p", "name": "get_pokedex_entry", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "p", "content": [{"text": BULBASAUR}]}}]},
        {"role": "assistant", "content": [{"text": "BULBASAUR. El Pokémon Semilla. 999"}]},
    ]
    assert checks.evidence_from_messages(messages) == ["háblame de bulbasaur", BULBASAUR]


def test_evaluate_turn_labels_have_no_dots():
    for turn in _turns(SESSIONS["session6"]):
        for r in checks.evaluate_turn(turn):
            assert "." not in r.label
            assert r.assessment in ("pass", "fail")
