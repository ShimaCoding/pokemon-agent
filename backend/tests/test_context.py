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
    # Turns 3-6 remain: squirtle first.
    assert messages[0]["content"] == [{"text": "ahora squirtle"}]
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


# Input budget for a Pokédex question deep in a conversation (#18 acceptance:
# p95 < 5,000 input tokens in sessions of 5+ turns). A Pokédex request makes 2
# LLM calls and both re-send the history, so it costs about
# FIRST_TURN_INPUT + 2 × history. FIRST_TURN_INPUT is the measured input of
# "háblame de bulbasaur" in a new conversation with #16-#18 (no history).
FIRST_TURN_INPUT = 3_412
INPUT_BUDGET = 5_000
LLM_CALLS_PER_POKEDEX_TURN = 2


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
    # Conservative: the fixture still carries the old `skills` call pairs.
    estimated_input = FIRST_TURN_INPUT + LLM_CALLS_PER_POKEDEX_TURN * max(compacted)
    assert estimated_input < INPUT_BUDGET


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


def test_stub_forbids_quoting_from_memory():
    # A past result's data must be re-fetched, never recalled (an "omitido" stub
    # let a comparison turn quote an earlier Pokémon's flavor text).
    assert "ya no están disponibles" in TOOL_RESULT_STUB
    assert "No los cites de memoria" in TOOL_RESULT_STUB
    assert "vuelve a llamar a la herramienta" in TOOL_RESULT_STUB
