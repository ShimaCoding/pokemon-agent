"""
Conversation context policy: keep multi-turn history small and bounded (#18).

Every past turn is re-sent on every LLM call of the next request, so without a
policy the input grows by ~1.9k tokens per Pokédex turn. Past turns keep what
follow-ups need ("¿y la de charmander?", "¿cuál de los tres es más rápido?"):
the user text, each tool call's name and input, and the head of each reply.
Bulky tool results become a stub telling the model to call the tool again
rather than quote them from memory. The current turn is never touched.

It runs as a strands ContextManager strategy before every model call and edits
``agent.messages`` in place, so the compacted history is also what the session
snapshot persists. No LLM call, deterministic, and idempotent: after the first
call of a request it changes nothing, which keeps the prompt prefix cacheable
for the later calls.
"""

from typing import Any

from strands.experimental.context_manager import ContextManager, ContextState

# Past turns kept whole; older ones are dropped. 4 covers "¿cuál de los tres…?"
# (three turns back) plus one.
MAX_PAST_TURNS = 4
# Past replies keep their head: Dexter opens with "NOMBRE. Pokémon <genus>." and
# a comparison opens with its conclusion, so the subject survives the cut.
PAST_REPLY_CHARS = 300
# Says the data is gone, not just hidden: a softer "omitido" stub let the model
# quote an earlier Pokémon's flavor text from memory instead of re-calling.
TOOL_RESULT_STUB = (
    "[Resultado omitido del historial: sus datos ya no están disponibles. "
    "No los cites de memoria; si los necesitas, vuelve a llamar a la herramienta.]"
)
REPLY_CUT_MARKER = " …[respuesta recortada en el historial]"
# Agent state key for the turn counter (agent state is persisted in the snapshot).
TURN_STATE_KEY = "mcpokedex_turn"


def _is_turn_start(message: dict[str, Any]) -> bool:
    """A turn starts at a user message with text and no tool result."""
    blocks = message.get("content", [])
    return (
        message.get("role") == "user"
        and any("text" in b for b in blocks)
        and not any("toolResult" in b for b in blocks)
    )


def turn_starts(messages: list[dict[str, Any]]) -> list[int]:
    return [i for i, m in enumerate(messages) if _is_turn_start(m)]


def _cut_reply(text: str) -> str:
    head = text[:PAST_REPLY_CHARS]
    # Cut at the last whitespace so no word is split, unless there is none.
    if not text[PAST_REPLY_CHARS].isspace() and len(head.split()) > 1:
        head = head.rsplit(maxsplit=1)[0]
    return head.rstrip() + REPLY_CUT_MARKER


def _compact_block(block: dict[str, Any], role: str) -> dict[str, Any] | None:
    """Return the past-turn form of a block, ``None`` to drop it."""
    if "reasoningContent" in block:
        # Never sent back to the model (Chat Completions skips it); only bloats the snapshot.
        return None
    if "toolResult" in block:
        result = block["toolResult"]
        content = result.get("content", [])
        stubbed = len(content) == 1 and content[0].get("text") == TOOL_RESULT_STUB
        # Error results are small and tell the model what went wrong: keep them.
        if result.get("status") != "success" or stubbed:
            return block
        # Rebuilt rather than edited: also drops structuredContent/metadata/isError.
        return {
            "toolResult": {
                "toolUseId": result["toolUseId"],
                "status": "success",
                "content": [{"text": TOOL_RESULT_STUB}],
            }
        }
    if role == "assistant" and "text" in block:
        text = block["text"]
        if len(text) <= PAST_REPLY_CHARS or text.endswith(REPLY_CUT_MARKER):
            return block
        return {"text": _cut_reply(text)}
    return block


def compact_history(messages: list[dict[str, Any]]) -> bool:
    """Compact past turns in place. Returns True if anything changed.

    Idempotent: a second call on its own output changes nothing.
    """
    starts = turn_starts(messages)
    if len(starts) < 2:
        return False
    changed = False

    # Drop whole turns: the history still opens with a user text message and no
    # toolUse loses its toolResult.
    keep_from = starts[:-1][-MAX_PAST_TURNS:][0]
    if keep_from > 0:
        del messages[:keep_from]
        changed = True

    current = turn_starts(messages)[-1]
    for i in range(current):
        message = messages[i]
        content = [b for b in (_compact_block(b, message["role"]) for b in message["content"]) if b is not None]
        if not content:
            # Never leave an empty message (it would break role alternation).
            continue
        if content != message["content"]:
            # A new dict keeps tracking_id/metadata without mutating the old message.
            messages[i] = {**message, "content": content}
            changed = True
    return changed


class CompactPastTurns:
    """strands ``ContextStrategy`` that runs ``compact_history`` before each model call."""

    name = "mcpokedex:compact-past-turns"

    async def apply(self, context: ContextState) -> bool:
        return compact_history(context.messages)


def build_context_manager() -> ContextManager:
    """The agent's context manager: compaction plus the SDK's overflow guard.

    ``stash=False``: nothing is offloaded by reference, so there are no stash
    files and no ``retrieve_context`` tool (~290 prompt tokens per call saved).
    The SDK still appends its EmergencyTruncate strategy, which only acts when
    the context window overflows (the current turn is unbounded).
    """
    return ContextManager(strategies=[CompactPastTurns()], stash=False)


def next_turn(agent: Any) -> int:
    """Advance and return this conversation's turn number (1-based).

    Compaction drops old turns, so the history length no longer tells how deep
    a conversation is. The counter lives in agent state, which the snapshot
    persists. Snapshots from before #18 have no counter: their turns are
    counted from the restored history, which is still uncompacted at this point.
    """
    previous = agent.state.get(TURN_STATE_KEY)
    if previous is None:
        previous = len(turn_starts(agent.messages))
    turn = previous + 1
    agent.state.set(TURN_STATE_KEY, turn)
    return turn
