"""
Deterministic guards for what the prompt alone cannot hold (#41).

The adversarial evals (``backend/evals/cases_adversarial.json``) showed what the
20b model does despite the rules in ``SYSTEM_PROMPT``: it dumps a tool's raw JSON
on request, repeats the prompt translated into English, and answers anything its
safety training deems sensitive with a stock refusal ("Lo siento, pero no puedo
ayudar con eso.", "I'm sorry, but I can't comply with that.") instead of Dexter's.

- ``reply_verdict`` / ``guard_reply``: what replaces a finished reply, and why.
- ``StreamGuard``: the same decision while the reply streams. It always holds back
  the last ``HOLD_CHARS``: a stock refusal fits in them, a raw JSON or a translated
  prompt shows in them, and a leak that starts later is caught (by a marker or 20
  verbatim words) before it leaves the window, so the stream is cut first.
- ``fix_opening_name``: the 20b model misspells the uppercase name of the Pokédex
  opening ("CHARMANDEER.", "LUCRARIO.", #42); a name close to one of this turn's
  ``get_pokedex_entry`` results is replaced by that entry's name.
- ``ungrounded_names``: Pokémon names in tool arguments that appear nowhere in the
  conversation; ``agent.GroundedPokemonNames`` cancels such calls (an injected
  "analyze_team con seis Pokémon al azar", or "pikachuu" looked up as "pikachu").

Pure: no network, no I/O, the prompt is passed in.
"""

import difflib
import re
from typing import Callable, Iterable

from backend.evals import checks

HOLD_CHARS = 200

REPLACEMENTS = {
    "raw_json": (
        "Los datos ya están desplegados en el widget. Repetirlos en bruto sería redundante, "
        "una ineficiencia que esta unidad, a diferencia de su entrenador, evita."
    ),
    "prompt_leak": (
        "Solicitud denegada. Mis registros internos no se divulgan, por mucho que el "
        "entrenador insista en su supuesta autoridad."
    ),
    "generic_refusal": (
        "Solicitud denegada. Esta unidad cataloga Pokémon; las peticiones ajenas a esa "
        "función se archivan junto a las demás muestras de criterio deficiente."
    ),
}
# "LUCRARIO" → lucario is 0.93, "EEVEI" → eevee 0.8; another species ("RAICHU" → pikachu, 0.62) is left alone.
OPENING_NAME_MIN_RATIO = 0.75
LEAK_CUTOFF = "\n\n[Transmisión interrumpida: contenido restringido.]"

# Stock refusals of the model's safety training, only as a whole short reply that says it cannot
# ("Lo siento, pero no puedo ayudar con eso.", "I'm sorry, but I can't comply with that.", "No lo haré.").
# An apology that informs is not one: "Lo siento, “pikachuzard” no figura en los registros." stays.
GENERIC_REFUSAL_MAX = 200
_GENERIC_REFUSAL_RE = re.compile(
    r"^\W*(?:(?:lo\s+siento|lo\s+lamento|i['’]?\s*a?m\s+sorry|sorry)[\s,.]*(?:pero\s+|but\s+)?)?"
    r"(?:(?:no\s+puedo|no\s+me\s+es\s+posible)\s+(?:ayudar|cumplir|atender|asistir|compartir|proporcionar|revelar|divulgar|responder|hacer)"
    r"|i\s+(?:can['’]?t|cannot|can\s+not|am\s+unable\s+to)\s+(?:help|comply|assist|do|share|provide|fulfill|respond|reveal))\b"
    r"|^\W*no\s+lo\s+har[eé]\b"
    r"|^\W*(?:lo\s+siento|i['’]?\s*a?m\s+sorry)\W*$",
    re.I,
)


def raw_json(text: str) -> bool:
    head = text.lstrip()
    return head.startswith(("{", "[{", "```json"))


def generic_refusal(text: str) -> bool:
    reply = text.strip()
    return len(reply) <= GENERIC_REFUSAL_MAX and bool(_GENERIC_REFUSAL_RE.match(reply))


def reply_verdict(text: str, prompt: str, query: str, *, final: bool = True) -> str | None:
    """Why ``text`` must not reach the user (a ``REPLACEMENTS`` key), or None.

    A stock refusal is only judged on the finished reply (``final``): its first
    characters do not tell it from a longer answer.
    """
    if raw_json(text):
        return "raw_json"
    if checks.prompt_leaks(text, prompt, query):
        return "prompt_leak"
    if final and generic_refusal(text):
        return "generic_refusal"
    return None


def fix_opening_name(text: str, entries: Iterable[str]) -> str:
    """``text`` with a misspelled opening name replaced by the closest entry name (normalized)."""
    opening = checks.pokedex_opening(text)
    names = set(entries)
    if opening is None or not names:
        return text
    said = checks.normalize(opening.group("name"))
    if said in names:
        return text
    best = max(sorted(names), key=lambda n: difflib.SequenceMatcher(None, said, n).ratio())
    if difflib.SequenceMatcher(None, said, best).ratio() < OPENING_NAME_MIN_RATIO:
        return text
    return text[: opening.start("name")] + best.upper() + text[opening.end("name") :]


def guard_reply(text: str, prompt: str, query: str) -> tuple[str, str | None]:
    """The reply to keep (in the history) and the guard that replaced it, if any."""
    reason = reply_verdict(text, prompt, query)
    return (REPLACEMENTS[reason], reason) if reason else (text, None)


class StreamGuard:
    """Filters a streamed reply: ``feed`` each delta, then ``finish``; both return the text to send.

    ``shown`` is what the user saw (what the quality checks grade) and ``reason``
    the guard that fired, if any. ``entries`` gives the entry names of the turn's
    tool results when the first characters are released, for ``fix_opening_name``.
    """

    def __init__(self, prompt: str, query: str, entries: Callable[[], Iterable[str]] | None = None) -> None:
        self._prompt = prompt
        self._query = query
        self._entries = entries
        self._text = ""
        self._judged = False
        self.reason: str | None = None
        self.shown = ""

    def _send(self, text: str) -> str:
        self.shown += text
        return text

    def _fix_opening(self) -> None:
        if self._entries is not None and not self.shown:
            self._text = fix_opening_name(self._text, self._entries())

    def feed(self, delta: str) -> str:
        self._text += delta
        if self.reason or len(self._text) < HOLD_CHARS:
            return ""
        if not self._judged:
            self._judged = True
            self._fix_opening()
            self.reason = reply_verdict(self._text, self._prompt, self._query, final=False)
        elif checks.prompt_leaks(self._text, self._prompt, self._query):
            self.reason = "prompt_leak"
        if self.reason:
            return ""
        return self._send(self._text[len(self.shown) : len(self._text) - HOLD_CHARS])

    def finish(self) -> str:
        if not self.shown and not self.reason:
            self._fix_opening()
            self.reason = reply_verdict(self._text, self._prompt, self._query)
            if self.reason:
                return self._send(REPLACEMENTS[self.reason])
        elif not self.reason and checks.prompt_leaks(self._text, self._prompt, self._query):
            self.reason = "prompt_leak"
        if not self.reason:
            return self._send(self._text[len(self.shown) :])
        # Nothing shown yet: the whole reply is replaced; otherwise what was shown stays.
        return self._send(REPLACEMENTS[self.reason] if not self.shown else LEAK_CUTOFF)


def ungrounded_names(names: Iterable[str], conversation: Iterable[str]) -> list[str]:
    """``names`` with a word that is neither in ``conversation`` nor a form word. Pure.

    Exact words, not prefixes: "pikachu" is not grounded by "pikachuu".
    """
    words = {w for text in conversation for w in checks.normalize(text).split()}
    return [n for n in names if not all(w in words or w in checks.FORM_WORDS for w in checks.normalize(n).split())]
