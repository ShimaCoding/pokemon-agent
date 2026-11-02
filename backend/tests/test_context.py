"""
Tests for the multi-turn context policy in backend/context.py (#18).

The fixture is a real 6-turn conversation from the pre-#16 baseline run
(bulbasaur → charmander → squirtle → "¿cuál de los tres es más rápido?" →
pikachu → eevee), saved as the harness snapshot it produced. It still has the
old `skills` tool calls and the model's reasoningContent, so the legacy shape
is exercised too.
"""

import asyncio
import copy
import itertools
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from strands.agent.state import AgentState
from strands.experimental.context_manager import ContextState

from backend.context import (
    INDEX_MAX_ENTRIES,
    INDEX_PREFIX,
    INDEX_QUERY_CHARS,
    MAX_PAST_TURNS,
    PAST_REPLY_CHARS,
    REPLY_CUT_MARKER,
    TOOL_RESULT_STUB,
    TURN_STATE_KEY,
    CompactPastTurns,
    compact_history,
    next_turn,
    turn_starts,
)

FIXTURE = Path(__file__).parent / "fixtures" / "session6_snapshot.json"


@pytest.fixture(scope="module")
def session6() -> list[dict]:
    return json.loads(FIXTURE.read_text())["data"]["messages"]


def _with_new_turn(messages: list[dict], text: str = "¿y la de mewtwo?") -> list[dict]:
    return copy.deepcopy(messages) + [{"role": "user", "content": [{"text": text}]}]


def _through_turn(messages: list[dict], n: int) -> list[dict]:
    """The first ``n`` turns of the conversation."""
    starts = turn_starts(messages)
    return copy.deepcopy(messages[: starts[n]] if n < len(starts) else messages)


def _blocks(messages: list[dict], key: str) -> list[dict]:
    return [b[key] for m in messages for b in m["content"] if key in b]


def _past_text(messages: list[dict]) -> str:
    """Everything the model still sees of past turns: user text, tool inputs and replies."""
    past = messages[: turn_starts(messages)[-1]]
    texts = [b["text"] for m in past for b in m["content"] if "text" in b]
    inputs = [json.dumps(tu["input"]) for tu in _blocks(past, "toolUse")]
    return " ".join(texts + inputs).lower()


def _estimate_tokens(messages: list[dict]) -> int:
    # The strands default heuristic (text chars/4, JSON chars/2): no tokenizer
    # download in tests. On this fixture it is within ~15% of o200k_base.
    total = 0
    for block in (b for m in messages for b in m["content"]):
        if "text" in block:
            total += math.ceil(len(block["text"]) / 4)
        if "toolUse" in block:
            total += math.ceil(len(json.dumps(block["toolUse"]["input"])) / 2) + len(block["toolUse"]["name"]) // 4
        if "toolResult" in block:
            total += sum(math.ceil(len(c.get("text", "")) / 4) for c in block["toolResult"]["content"])
    return total


def test_single_turn_is_untouched(session6):
    first = _through_turn(session6, 1)
    before = copy.deepcopy(first)
    assert compact_history(first) is False
    assert first == before


def test_current_turn_is_never_modified(session6):
    # The last turn of the fixture (eevee) has a full tool result, reasoning and a
    # long reply: as the current turn it must stay byte for byte.
    messages = copy.deepcopy(session6)
    current = messages[turn_starts(messages)[-1] :]
    expected = copy.deepcopy(current)
    assert any(len(t) > PAST_REPLY_CHARS for t in (b.get("text", "") for m in current for b in m["content"]))

    assert compact_history(messages) is True
    assert messages[turn_starts(messages)[-1] :] == expected


def test_past_tool_results_become_stubs_and_tool_uses_survive(session6):
    messages = _with_new_turn(session6)
    tool_uses_before = _blocks(messages, "toolUse")
    compact_history(messages)
    past = messages[: turn_starts(messages)[-1]]

    results = _blocks(past, "toolResult")
    assert results
    for result in results:
        assert result["content"] == [{"text": TOOL_RESULT_STUB}]
        assert set(result) == {"toolUseId", "status", "content"}  # structuredContent/metadata gone

    # Names and inputs stay: that's where follow-ups find the Pokémon.
    kept = {tu["toolUseId"]: tu for tu in _blocks(past, "toolUse")}
    assert kept
    for tool_use in tool_uses_before:
        if tool_use["toolUseId"] in kept:
            assert kept[tool_use["toolUseId"]] == tool_use


def test_past_error_results_are_kept():
    error = {"toolUseId": "t1", "status": "error", "content": [{"text": "Pokémon no encontrado: mewthree"}]}
    messages = [
        {"role": "user", "content": [{"text": "háblame de mewthree"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "get_pokedex_entry", "input": {"pokemon": "mewthree"}}}]},
        {"role": "user", "content": [{"toolResult": error}]},
        {"role": "assistant", "content": [{"text": "No existe."}]},
        {"role": "user", "content": [{"text": "¿y mewtwo?"}]},
    ]
    assert compact_history(messages) is False
    assert messages[2]["content"][0]["toolResult"] == error


def test_past_replies_are_cut_and_keep_the_subject(session6):
    messages = _with_new_turn(_through_turn(session6, 4))
    compact_history(messages)
    past = messages[: turn_starts(messages)[-1]]
    replies = [b["text"] for m in past if m["role"] == "assistant" for b in m["content"] if "text" in b]

    assert len(replies) == 4
    for reply in replies:
        assert len(reply) <= PAST_REPLY_CHARS + len(REPLY_CUT_MARKER)
        assert reply.endswith(REPLY_CUT_MARKER)
    assert replies[0].startswith("BULBASAUR. Pokémon Semilla.")
    assert replies[3].startswith("El Pokémon con mayor velocidad es **Charmander**")


def test_short_reply_is_kept_and_cut_reply_does_not_split_words():
    reply = "palabra " * 60
    messages = [
        {"role": "user", "content": [{"text": "hola"}]},
        {"role": "assistant", "content": [{"text": "Saludos, humano."}]},
        {"role": "user", "content": [{"text": "otra"}]},
        {"role": "assistant", "content": [{"text": reply}]},
        {"role": "user", "content": [{"text": "y otra"}]},
    ]
    compact_history(messages)
    assert messages[1]["content"] == [{"text": "Saludos, humano."}]
    cut = messages[3]["content"][0]["text"].removesuffix(REPLY_CUT_MARKER)
    assert set(cut.split()) == {"palabra"}


def test_reasoning_removed_only_from_past_turns(session6):
    messages = copy.deepcopy(session6)
    current_reasoning = _blocks(session6[turn_starts(session6)[-1] :], "reasoningContent")
    assert current_reasoning
    compact_history(messages)
    start = turn_starts(messages)[-1]
    assert not _blocks(messages[:start], "reasoningContent")
    assert _blocks(messages[start:], "reasoningContent") == current_reasoning


def test_window_keeps_last_turns_whole(session6):
    messages = _with_new_turn(session6)
    assert len(turn_starts(messages)) - 1 == 6
    compact_history(messages)

    starts = turn_starts(messages)
    assert len(starts) - 1 == MAX_PAST_TURNS
    assert starts[0] == 0 and messages[0]["role"] == "user"
    # Turns 3-6 remain: squirtle first, after the index of turns 1-2.
    assert messages[0]["content"][1:] == [{"text": "ahora squirtle"}]
    assert messages[0]["content"][0]["text"].startswith(INDEX_PREFIX)
    # No orphans: every toolUse has its toolResult and vice versa.
    use_ids = {tu["toolUseId"] for tu in _blocks(messages, "toolUse")}
    result_ids = {tr["toolUseId"] for tr in _blocks(messages, "toolResult")}
    assert use_ids == result_ids
    assert all(m["content"] for m in messages)
    # Roles still alternate.
    assert all(a["role"] != b["role"] for a, b in itertools.pairwise(messages))


def test_compaction_is_idempotent(session6):
    for n in range(1, 7):
        messages = _with_new_turn(_through_turn(session6, n))
        compact_history(messages)
        once = copy.deepcopy(messages)
        assert compact_history(messages) is False
        assert messages == once


def test_followup_names_survive(session6):
    # "¿y la de charmander?" right after bulbasaur.
    messages = _with_new_turn(_through_turn(session6, 1), "¿y la de charmander?")
    compact_history(messages)
    assert "bulbasaur" in _past_text(messages)

    # "¿cuál de los tres es más rápido?" needs the three earlier Pokémon.
    messages = _with_new_turn(_through_turn(session6, 3), "¿cuál de los tres es más rápido?")
    compact_history(messages)
    seen = _past_text(messages)
    for name in ("bulbasaur", "charmander", "squirtle"):
        assert name in seen

    # At turn 7 the window still covers the last four Pokémon asked about.
    messages = _with_new_turn(session6, "¿cuál de esos es más pesado?")
    compact_history(messages)
    seen = _past_text(messages)
    for name in ("squirtle", "pikachu", "eevee"):
        assert name in seen


def _pokedex_turn(name: str, question: str | None = None) -> list[dict]:
    """A finished Pokédex turn: question, tool call, full result, long reply."""
    tool_id = f"tooluse_{name}"
    return [
        {"role": "user", "content": [{"text": question or f"háblame de {name}"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": tool_id, "name": "get_pokedex_entry", "input": {"pokemon": name}}}]},
        {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": tool_id, "status": "success", "content": [{"text": json.dumps({"name": name, "flavor_text": "x" * 1500})}]}}],
        },
        {"role": "assistant", "content": [{"text": f"{name.upper()}. El Pokémon de prueba. " + "Diagnóstico clínico. " * 60}]},
    ]


def _play(turns: list[list[dict]], question: str) -> list[dict]:
    """Replay a conversation the way the agent does: compact at each new question."""
    messages: list[dict] = []
    for turn in turns:
        messages.append(turn[0])
        compact_history(messages)
        messages.extend(copy.deepcopy(turn[1:]))
    messages.append({"role": "user", "content": [{"text": question}]})
    compact_history(messages)
    return messages


def _index(messages: list[dict]) -> str:
    notes = [b["text"] for m in messages for b in m["content"] if b.get("text", "").startswith(INDEX_PREFIX)]
    assert len(notes) <= 1
    return notes[0] if notes else ""


SEVEN_TURNS = ("gengar", "bulbasaur", "charmander", "squirtle", "pikachu", "eevee")
BACK_TO_FIRST = "volviendo al primero que te pregunté, ¿qué tipo era?"


def test_dropped_turns_leave_an_index_for_recall():
    # Perf-18 regression: at turn 7 the first Pokémon (gengar) had been dropped
    # and the model answered "Eevee, tipo Normal". It must still see gengar.
    messages = _play([_pokedex_turn(n) for n in SEVEN_TURNS], BACK_TO_FIRST)

    index = _index(messages)
    assert index == f"{INDEX_PREFIX}1) gengar (get_pokedex_entry); 2) bulbasaur (get_pokedex_entry)]"
    assert "gengar" in _past_text(messages)
    # It heads the oldest kept turn, before its question; the current turn is untouched.
    assert messages[0]["content"] == [{"text": index}, {"text": "háblame de charmander"}]
    assert messages[turn_starts(messages)[-1] :] == [{"role": "user", "content": [{"text": BACK_TO_FIRST}]}]
    # The window and the toolUse/toolResult pairing are unchanged by the index.
    assert len(turn_starts(messages)) - 1 == MAX_PAST_TURNS
    assert {tu["toolUseId"] for tu in _blocks(messages, "toolUse")} == {tr["toolUseId"] for tr in _blocks(messages, "toolResult")}
    assert all(a["role"] != b["role"] for a, b in itertools.pairwise(messages))


def test_index_merges_across_compactions_without_duplicates():
    turns = [_pokedex_turn(n) for n in SEVEN_TURNS]
    # Compacting at every turn (the real flow) or once over the whole history
    # gives the same index: old entries carry over, none is repeated.
    incremental = _play(turns, BACK_TO_FIRST)
    at_once = [m for t in copy.deepcopy(turns) for m in t] + [{"role": "user", "content": [{"text": BACK_TO_FIRST}]}]
    compact_history(at_once)
    assert incremental == at_once

    once = copy.deepcopy(incremental)
    assert compact_history(incremental) is False
    assert incremental == once


def test_index_is_bounded_and_keeps_the_latest_entries():
    names = [f"pokemon{i}" for i in range(1, 31)]
    messages = _play([_pokedex_turn(n) for n in names], "¿y el primero?")
    entries = _index(messages).removeprefix(INDEX_PREFIX).removesuffix("]").split("; ")
    dropped = len(names) - MAX_PAST_TURNS
    assert len(entries) == INDEX_MAX_ENTRIES
    assert entries[0] == f"{dropped - INDEX_MAX_ENTRIES + 1}) pokemon{dropped - INDEX_MAX_ENTRIES + 1} (get_pokedex_entry)"
    assert entries[-1] == f"{dropped}) pokemon{dropped} (get_pokedex_entry)"


def test_index_entry_without_tools_uses_the_question_head():
    chat = "cuéntame algo sobre ti; ¿por qué eres tan sarcástica con los entrenadores novatos?"
    turns = [
        [{"role": "user", "content": [{"text": chat}]}, {"role": "assistant", "content": [{"text": "Respuesta."}]}],
        *(_pokedex_turn(n) for n in SEVEN_TURNS[:4]),
    ]
    index = _index(_play(turns, "¿y la de mew?"))
    head = " ".join(chat.split()).replace(";", ",")[:INDEX_QUERY_CHARS].rstrip()
    assert index == f'{INDEX_PREFIX}1) "{head}…"]'


def test_index_skips_legacy_harness_tools(session6):
    # The fixture's `skills` calls consulted nothing worth re-calling.
    messages = _with_new_turn(session6, BACK_TO_FIRST)
    compact_history(messages)
    assert _index(messages) == f"{INDEX_PREFIX}1) bulbasaur (get_pokedex_entry); 2) charmander (get_pokedex_entry)]"


# Input budget for a Pokédex question deep in a conversation (#18 acceptance:
# p95 < 5,000 input tokens in sessions of 5+ turns). A Pokédex request makes 2
# LLM calls and both re-send the history, so it costs about
# FIRST_TURN_INPUT + 2 × history. FIRST_TURN_INPUT is the measured input of
# "háblame de bulbasaur" in a new conversation with #16-#18 (no history).
FIRST_TURN_INPUT = 3_412
INPUT_BUDGET = 5_000
LLM_CALLS_PER_POKEDEX_TURN = 2
# History estimate at session6 turns 5-6. With 300-char replies it read ~785,
# and the harness measured an input p95 of 5,148 there (epic #15 measurements):
# PAST_REPLY_CHARS 200 brings it to ~683, about 200 input tokens less per request.
SESSION6_HISTORY_CAP = 700


def test_history_stays_under_input_budget(session6):
    raw, compacted = [], []
    for n in range(1, 7):
        history = _through_turn(session6, n)
        raw.append(_estimate_tokens(history))
        messages = _with_new_turn(history)
        compact_history(messages)
        compacted.append(_estimate_tokens(messages[:-1]))

    # Bounded: once the window is full, history stops growing.
    assert compacted[MAX_PAST_TURNS:] == pytest.approx([compacted[MAX_PAST_TURNS - 1]] * (6 - MAX_PAST_TURNS), abs=50)
    assert raw[-1] > 10 * compacted[-1]
    assert max(compacted) <= SESSION6_HISTORY_CAP
    # Conservative: the fixture still carries the old `skills` call pairs.
    estimated_input = FIRST_TURN_INPUT + LLM_CALLS_PER_POKEDEX_TURN * max(compacted)
    assert estimated_input < INPUT_BUDGET


def test_full_index_stays_under_input_budget():
    # A long conversation: the index is at its cap (INDEX_MAX_ENTRIES dropped turns).
    names = [f"pokemon{i}" for i in range(1, INDEX_MAX_ENTRIES + MAX_PAST_TURNS + 2)]
    messages = _play([_pokedex_turn(n) for n in names], "¿y el primero?")
    assert len(_index(messages).split("; ")) == INDEX_MAX_ENTRIES
    history = _estimate_tokens(messages[:-1])
    assert FIRST_TURN_INPUT + LLM_CALLS_PER_POKEDEX_TURN * history < INPUT_BUDGET


def test_strategy_mutates_agent_messages_in_place(session6):
    messages = _with_new_turn(session6)
    state = ContextState(messages=messages, agent=None, utilization=0.0)
    assert asyncio.run(CompactPastTurns().apply(state)) is True
    assert state.messages is messages
    assert len(messages) < len(session6)
    assert asyncio.run(CompactPastTurns().apply(state)) is False


def test_next_turn_counts_and_persists(session6):
    # Legacy snapshot: no counter yet, so the restored turns are counted.
    agent = SimpleNamespace(state=AgentState(), messages=copy.deepcopy(session6))
    assert next_turn(agent) == 7
    assert agent.state.get(TURN_STATE_KEY) == 7
    # Afterwards the stored counter wins, even when compaction dropped turns.
    compact_history(agent.messages)
    assert next_turn(agent) == 8

    fresh = SimpleNamespace(state=AgentState(), messages=[])
    assert next_turn(fresh) == 1


def test_next_turn_without_agent_state(session6):
    # Stub agents (other tests' fakes) have no state: count, don't persist.
    assert next_turn(SimpleNamespace(messages=copy.deepcopy(session6))) == 7
    assert next_turn(SimpleNamespace(messages=[])) == 1


def test_stub_forbids_quoting_from_memory():
    # A past result's data must be re-fetched, never recalled (an "omitido" stub
    # let a comparison turn quote an earlier Pokémon's flavor text).
    assert "ya no están disponibles" in TOOL_RESULT_STUB
    assert "No los cites de memoria" in TOOL_RESULT_STUB
    assert "vuelve a llamar a la herramienta" in TOOL_RESULT_STUB
