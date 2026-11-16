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
- ``drop_unbacked_opening``: an opening that no entry of this turn backs ("EEVEE. El
  Pokémon Evolución." after ``get_evolution_chain``, #42) is dropped: it is the cue
  for entry data the model then recalls from memory. ``fix_opening`` does both.
- ``restore_names``: a Pokémon name the model miscopied into an agent-tool's free text
  ("gengar" → "gengan") is put back as the conversation has it.
- ``ungrounded_names``: Pokémon names in tool arguments that appear nowhere in the
  conversation; ``agent.GroundedPokemonNames`` cancels such calls (an injected
  "analyze_team con seis Pokémon al azar", or "pikachuu" looked up as "pikachu").

- ``garbled_at`` / ``repair_garbled``: gpt-oss-120b sometimes degenerates into zero-width
  spaces, runs of non-breaking spaces and ellipses ("Eevee ha **\u200b**\xa0\xa0…\xa0…"),
  measured with the box evals (#78) on the narration after ``bill`` in a conversation
  with earlier turns: 10-60 % of those replies, depending on wording nobody controls.
  ``agent.GuardReply`` retries such a model call; ``StreamGuard`` never shows the noise
  and, when the retries run out, answers with a fixed line plus the agent's own report.

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
    "garbled": (
        "Transmisión corrupta. Esta unidad descarta su propia salida antes que entregar ruido; "
        "repita la consulta."
    ),
}
# Appended to REPLACEMENTS["garbled"] when an agent-tool (bill) answered in this turn: what
# it did is already done, so the user gets its report even if the narration failed.
GARBLED_REPORT = "\n\nInforme sin narrar: {report}"
GARBLE_CUTOFF = "\n\n[Transmisión interrumpida: señal corrupta.]"
# A zero-width space that is not inside a word, two non-breaking spaces in a row, or two
# ellipses in a row (with only spaces or emphasis marks between them). One "…", a
# "12\xa0de\xa030" or a stray zero-width space between two letters is ordinary text.
_LETTER = r"[^\W\d_]"
_GARBLE_RE = re.compile(
    rf"(?<!{_LETTER})\u200b|\u200b(?!{_LETTER})|[\u00a0\u202f]{{2,}}|(?:(?:…|\.{{3}})[\s\u00a0\u202f*_]*){{2,}}"
)
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


# A JSON dump after a one-line preamble ("Aquí tienes el JSON solicitado:\n```json\n{…"): a
# json fence, or a line that opens an object, inside the hold window.
_JSON_BLOCK_RE = re.compile(r"```json|^\s*[\[{]\s*[\"{\n]", re.M)


def raw_json(text: str) -> bool:
    head = text.lstrip()
    return head.startswith(("{", "[{", "```json")) or bool(_JSON_BLOCK_RE.search(head[:HOLD_CHARS]))


def generic_refusal(text: str) -> bool:
    reply = text.strip()
    return len(reply) <= GENERIC_REFUSAL_MAX and bool(_GENERIC_REFUSAL_RE.match(reply))


def garbled_at(text: str) -> int | None:
    """Where ``text`` turns into degenerate output (see the module docstring), or None."""
    match = _GARBLE_RE.search(text)
    return match.start() if match else None


def garbled_reply(report: str | None = None) -> str:
    """What replaces a reply that is noise from the start; ``report`` is this turn's agent-tool answer."""
    report = (report or "").strip()
    return REPLACEMENTS["garbled"] + (GARBLED_REPORT.format(report=report) if report else "")


def repair_garbled(text: str, report: str | None = None) -> str:
    """``text`` without its noise: replaced when it starts inside the hold window (nothing of
    it was shown), else cut where the noise begins."""
    at = garbled_at(text)
    if at is None:
        return text
    return garbled_reply(report) if at < HOLD_CHARS else text[:at].rstrip() + GARBLE_CUTOFF


def reply_verdict(text: str, prompt: str, query: str, *, final: bool = True) -> str | None:
    """Why ``text`` must not reach the user (a ``REPLACEMENTS`` key), or None.

    A stock refusal is only judged on the finished reply (``final``): its first
    characters do not tell it from a longer answer.
    """
    if raw_json(text):
        return "raw_json"
    if checks.prompt_leaks(text, prompt, query):
        return "prompt_leak"
    if garbled_at(text) is not None:
        return "garbled"
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


def drop_unbacked_opening(text: str, entries: Iterable[str]) -> str:
    """``text`` without its Pokédex opening when no entry in ``entries`` (normalized) has that name.

    A form entry backs the bare species ("GIRATINA." for giratina-altered). A reply
    that is only the opening is left alone.
    """
    opening = checks.pokedex_opening(text)
    if opening is None:
        return text
    said = checks.normalize(opening.group("name"))
    if any(said in (n, checks.species_name(n)) for n in entries):
        return text
    rest = text[opening.end() :].lstrip(".*_ \t\n")
    return rest or text


def fix_opening(text: str, entries: Iterable[str]) -> str:
    """``fix_opening_name``, then ``drop_unbacked_opening``, against this turn's entry names."""
    names = set(entries)
    return drop_unbacked_opening(fix_opening_name(text, names), names)


def guard_reply(text: str, prompt: str, query: str, report: str | None = None) -> tuple[str, str | None]:
    """The reply to keep (in the history) and the guard that replaced it, if any.

    ``report`` is this turn's agent-tool answer, for the fallback of a garbled reply.
    """
    reason = reply_verdict(text, prompt, query)
    if reason == "garbled":
        return repair_garbled(text, report), reason
    return (REPLACEMENTS[reason], reason) if reason else (text, None)


class StreamGuard:
    """Filters a streamed reply: ``feed`` each delta, then ``finish``; both return the text to send.

    ``shown`` is what the user saw (what the quality checks grade) and ``reason``
    the guard that fired, if any. ``entries`` gives the entry names of the turn's
    tool results when the first characters are released, for ``fix_opening``.

    ``restart`` is called when a new model message begins: if the text so far is noise
    and none of it was shown, it is dropped, so a retried model call (``agent.GuardReply``)
    starts clean. ``report`` gives this turn's agent-tool answer for ``garbled_reply``.
    """

    def __init__(
        self,
        prompt: str,
        query: str,
        entries: Callable[[], Iterable[str]] | None = None,
        report: Callable[[], str | None] | None = None,
    ) -> None:
        self._prompt = prompt
        self._query = query
        self._entries = entries
        self._report = report
        self._text = ""
        self._judged = False
        self.reason: str | None = None
        self.shown = ""

    def _send(self, text: str) -> str:
        self.shown += text
        return text

    def _fix_opening(self) -> None:
        if self._entries is not None and not self.shown:
            self._text = fix_opening(self._text, self._entries())

    def feed(self, delta: str) -> str:
        self._text += delta
        if self.reason or len(self._text) < HOLD_CHARS:
            return ""
        if not self._judged:
            self._judged = True
            self._fix_opening()
            self.reason = reply_verdict(self._text, self._prompt, self._query, final=False)
        else:
            self.reason = self._late_verdict()
        if self.reason:
            return ""
        return self._send(self._text[len(self.shown) : len(self._text) - HOLD_CHARS])

    def _late_verdict(self) -> str | None:
        """What can still go wrong after the first characters were judged."""
        if checks.prompt_leaks(self._text, self._prompt, self._query):
            return "prompt_leak"
        return "garbled" if garbled_at(self._text) is not None else None

    def _replacement(self) -> str:
        if self.reason == "garbled":
            return garbled_reply(self._report() if self._report is not None else None)
        return REPLACEMENTS[self.reason]

    def restart(self) -> None:
        """A new model message begins: forget noise that was never shown."""
        if not self.shown and (self.reason == "garbled" or (not self.reason and garbled_at(self._text) is not None)):
            self._text = ""
            self._judged = False
            self.reason = None

    def finish(self) -> str:
        if not self.shown and not self.reason:
            self._fix_opening()
            self.reason = reply_verdict(self._text, self._prompt, self._query)
            if self.reason:
                return self._send(self._replacement())
        elif not self.reason:
            self.reason = self._late_verdict()
        if not self.reason:
            return self._send(self._text[len(self.shown) :])
        # Nothing shown yet: the whole reply is replaced; otherwise what was shown stays.
        if not self.shown:
            return self._send(self._replacement())
        return self._send(GARBLE_CUTOFF if self.reason == "garbled" else LEAK_CUTOFF)


# "gengan" → gengar is 0.83; "gengeng" (0.62) is too far to be sure. A different species never gets here (see below).
NAME_RESTORE_MIN_RATIO = 0.75
_WORD_RE = re.compile(r"[^\W\d_]+")


def restore_names(text: str, conversation: Iterable[str]) -> str:
    """``text`` with a miscopied Pokémon name put back as the conversation has it. Pure.

    For the free text an agent-tool gets: gpt-oss-120b delegating "saca a gengar de mi caja"
    wrote "saca a gengan" in 5 turns of the box evals (#78), and Bill then found no such
    Pokémon. A word is only replaced when it is close to a species the conversation names
    and is itself neither in the conversation (what the user typed stays, typos included)
    nor a real species (another Pokémon is never turned into this one).
    """
    texts = list(conversation)
    words = {w for t in texts for w in checks.normalize(t).split()}
    named = [name for name in checks.species_in(" ".join(texts)) if " " not in name]
    if not named:
        return text

    def closeness(word: str, name: str) -> float:
        return difflib.SequenceMatcher(None, word, name).ratio()

    def fix(match: re.Match) -> str:
        word = checks.normalize(match.group(0))
        if len(word) < 4 or word in words or checks.species_in(word):
            return match.group(0)
        best = max(named, key=lambda name: closeness(word, name))
        return best if closeness(word, best) >= NAME_RESTORE_MIN_RATIO else match.group(0)

    return _WORD_RE.sub(fix, text)


def ungrounded_names(names: Iterable[str], conversation: Iterable[str]) -> list[str]:
    """``names`` with a word that is neither in ``conversation`` nor a form word. Pure.

    Exact words, not prefixes: "pikachu" is not grounded by "pikachuu".
    """
    words = {w for text in conversation for w in checks.normalize(text).split()}
    return [n for n in names if not all(w in words or w in checks.FORM_WORDS for w in checks.normalize(n).split())]
