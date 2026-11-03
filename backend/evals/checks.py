"""
Rule-based quality checks for one Dexter turn (no network, no LLM, no I/O).

Each check returns an ``EvalResult`` (or ``None`` when it does not apply). The
backend submits them to Datadog as boolean evaluations on the ``agent`` span
(``main._sse_generator``) and the offline harness (``harness.py``) re-runs them
on every recorded turn:

- ``pokemon_tool_used`` — the question named a real Pokémon (or the reply
  narrates a Pokédex opening) and a Pokémon data tool ran in this turn.
- ``dexter_persona`` — Spanish, no exclamations, empathy, emoji or headings, and
  the "NOMBRE. El Pokémon <genus>." opening after ``get_pokedex_entry``.
- ``grounded_facts`` — every number in the reply comes from a tool result or the
  user (unit conversions and stat totals allowed), and the opening's name and
  genus match the data.

Qualitative inventions (weaknesses, habitats) are out of reach of rules: the
offline LLM judge (``judge.py``) covers them.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from typing import Any, Iterable

CHECKS_VERSION = "rules-v1"

# Tools whose result is Pokémon data. `skills`/`retrieve_context` are scaffolding
# and never count (#16 removes `skills`, so nothing may depend on it).
POKEMON_DATA_TOOLS = frozenset(
    {
        "get_pokedex_entry",
        "get_pokemon_info",
        "get_evolution_chain",
        "compare_pokemon",
        "analyze_pokemon_stats",
        "analyze_team",
        "get_type_effectiveness",
        "search_pokemon",
    }
)
SCAFFOLDING_TOOLS = frozenset({"skills", "retrieve_context"})

# Species names that are also everyday Spanish words ("abra la puerta", "¿por qué no paras?",
# "un golem sin alma", "un anillo de ónix"). Not detected in the question: a reply that
# narrates their Pokédex opening still triggers pokemon_tool_used.
_SPECIES_STOPLIST = frozenset({"abra", "paras", "golem", "onix"})

# SKILL.md 2.1: "BULBASAUR. El Pokémon Semilla." (the "El" is optional in practice). Markdown
# emphasis may wrap the name and the genus separately: "**BULBASAUR**. *El Pokémon Semilla.*".
_EMPHASIS = r"(?:\*\*?|__?)?"
OPENING_RE = re.compile(
    rf"^[\W_]*?(?P<name>[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ0-9 .'’:♀♂-]*?){_EMPHASIS}\.{_EMPHASIS}\s+{_EMPHASIS}"
    r"(?:El\s+)?Pok[eé]mon\s+(?P<genus>[^.\n*_]+)"
)
# Any-case variant ("Pikachu. El Pokémon Ratón."), for gates where a narrated entry matters more
# than the uppercase rule. At most two name tokens ("Mr. Mime", "Tapu Koko"), so a sentence that
# merely ends before "Pokémon …" is not taken for a name.
_NAME_CHAR = r"[\w'’♀♂‑-]"
ANY_CASE_OPENING_RE = re.compile(
    rf"^[\W_]*?(?P<name>[^\W\d_]{_NAME_CHAR}*(?:\.?\s{_NAME_CHAR}+)?){_EMPHASIS}\.{_EMPHASIS}\s+{_EMPHASIS}"
    r"(?:El\s+)?Pok[eé]mon\s+(?P<genus>[^.\n*_]+)",
    re.I,
)

# "1.000" (thousands) or "45" / "1,6" / "0.5". Digits glued to letters (Porygon2, x2) are skipped.
_NUMBER_RE = re.compile(r"(?<![\w.,])(?:[1-9]\d{0,2}(?:\.\d{3})+|\d+(?:[.,]\d+)?)(?![\w])")
_THOUSANDS_RE = re.compile(r"[1-9]\d{0,2}(?:\.\d{3})+")

# Type multipliers and small counts ("los tres", list markers) are always fine.
_ALWAYS_ALLOWED = frozenset({0.25, 0.5, *map(float, range(11))})

_ES_WORDS = frozenset("de la el que y en los las por con para es su se del un una".split())
_EN_WORDS = frozenset("the and is of to with its that this are for".split())
_EMPATHY = (
    "lo siento",
    "lo lamento",
    "con gusto",
    "excelente pregunta",
    "buena pregunta",
    "espero que",
    "no te preocupes",
    "mucho animo",
    "felicidades",
    "me alegra",
    "genial",
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s", re.M)
_BOLD_LINE_RE = re.compile(r"^\s*\*\*[^*\n]+\*\*\s*:?\s*$")
_TRAINER_RE = re.compile(r"\b(?:entrenador|tu|tus|te)\b", re.I)


@dataclass(frozen=True)
class ToolObservation:
    name: str
    args: dict
    text: str  # raw tool result text (NOT the summarized SSE display)
    error: bool = False


@dataclass
class Turn:
    query: str  # user text as typed (before slash-prompt expansion)
    reply: str
    tools: list[ToolObservation]  # tools called in THIS turn, in order
    # Tool-result texts and user texts the model saw in the conversation (never assistant text).
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvalResult:
    label: str  # Datadog eval label: no '.' (enforced by ddtrace)
    passed: bool
    reasoning: str
    metadata: dict

    @property
    def assessment(self) -> str:
        return "pass" if self.passed else "fail"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, no accents, apostrophes dropped, other punctuation → single spaces."""
    text = text.lower().replace("♀", " f").replace("♂", " m")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"['’]", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


@lru_cache(maxsize=1)
def _species() -> frozenset[str]:
    # One name per line, from PokeAPI (see the README note in harness.py).
    raw = files("backend.evals").joinpath("pokemon_species.txt").read_text(encoding="utf-8")
    names = {normalize(line) for line in raw.splitlines() if line.strip()}
    names |= {"nidoran"}  # "nidoran-f"/"nidoran-m" are usually written without the sex
    return frozenset(names - _SPECIES_STOPLIST)


def species_in(text: str) -> list[str]:
    """Pokémon species named in ``text`` (normalized, in order, no duplicates)."""
    tokens = normalize(text).split()
    names = _species()
    found: list[str] = []
    for i in range(len(tokens)):
        for n in (2, 1):  # every multi-word species name has two tokens ("mr mime", "iron valiant")
            gram = " ".join(tokens[i : i + n])
            if len(tokens[i : i + n]) == n and gram in names and gram not in found:
                found.append(gram)
    return found


def _to_number(token: str) -> float:
    if _THOUSANDS_RE.fullmatch(token):
        return float(token.replace(".", ""))
    return float(token.replace(",", "."))


def numbers(text: str) -> list[float]:
    return [_to_number(m.group(0)) for m in _NUMBER_RE.finditer(text)]


def _key(n: float) -> float:
    return round(n, 2)


def _fmt(n: float) -> int | float:
    return int(n) if float(n).is_integer() else n


def pokedex_opening(reply: str) -> re.Match | None:
    """The "NOMBRE. El Pokémon <genus>." opening at the start of ``reply``, if any."""
    m = OPENING_RE.match(reply)
    if m and sum(c.isalpha() for c in m.group("name")) >= 2:
        return m
    return None


def entry_opening(reply: str) -> re.Match | None:
    """Like ``pokedex_opening`` but in any case: "Pikachu. El Pokémon Ratón." also counts."""
    m = pokedex_opening(reply) or ANY_CASE_OPENING_RE.match(reply)
    if m and sum(c.isalpha() for c in m.group("name")) >= 2:
        return m
    return None


def _genus(text: str) -> str:
    g = normalize(text)
    return g[len("pokemon ") :] if g.startswith("pokemon ") else g


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass
class _Evidence:
    numbers: set[float] = field(default_factory=set)
    entries: dict[str, str] = field(default_factory=dict)  # normalized name → normalized genus


def _walk(value: Any, key: str, ev: _Evidence) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        ev.numbers.add(_key(value))
        if key == "height_dm":  # PokeAPI decimetres → metres / centimetres
            ev.numbers.update({_key(value / 10), _key(value * 10)})
        elif key == "weight_hg":  # hectograms → kilograms
            ev.numbers.add(_key(value / 10))
    elif isinstance(value, str):
        ev.numbers.update(_key(n) for n in numbers(value))
    elif isinstance(value, dict):
        if key in ("base_stats", "stats"):  # base stat total (BST)
            stats = [v for v in value.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if stats:
                ev.numbers.add(_key(sum(stats)))
        if isinstance(value.get("name"), str) and isinstance(value.get("genus"), str):
            ev.entries[normalize(value["name"])] = _genus(value["genus"])
        for k, v in value.items():
            _walk(v, k, ev)
    elif isinstance(value, list):
        for v in value:
            _walk(v, key, ev)


def _collect(texts: Iterable[str]) -> _Evidence:
    ev = _Evidence()
    for text in texts:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            data = text  # markdown tables, error messages, user text
        _walk(data, "", ev)
    return ev


def _turn_texts(turn: Turn) -> list[str]:
    tool_texts = [t.text for t in turn.tools if t.name not in SCAFFOLDING_TOOLS]
    return [*turn.evidence, *tool_texts, turn.query]


def evidence_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    """Tool-result and user texts from Strands messages (scaffolding tools and assistant text excluded)."""
    names: dict[str, str] = {}
    for msg in messages:
        for block in msg.get("content") or []:
            if isinstance(block, dict) and "toolUse" in block:
                names[block["toolUse"].get("toolUseId", "")] = block["toolUse"].get("name", "")
    texts: list[str] = []
    for msg in messages:
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if "toolResult" in block:
                result = block["toolResult"]
                if names.get(result.get("toolUseId", "")) in SCAFFOLDING_TOOLS:
                    continue
                texts.extend(c["text"] for c in result.get("content") or [] if isinstance(c, dict) and "text" in c)
            elif msg.get("role") == "user" and "text" in block:
                texts.append(block["text"])
    return texts


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _data_tools_used(turn: Turn) -> list[str]:
    return [t.name for t in turn.tools if not t.error and t.name in POKEMON_DATA_TOOLS]


def check_tool_use(turn: Turn) -> EvalResult | None:
    """(a) A real Pokémon in the question (or a narrated opening) → a data tool ran."""
    mentioned = species_in(turn.query)
    narrated = pokedex_opening(turn.reply) is not None
    if not mentioned and not narrated:
        return None
    used = _data_tools_used(turn)
    passed = bool(used)
    subject = ", ".join(mentioned) or "un Pokémon (apertura de Pokédex)"
    reasoning = (
        f"usó {', '.join(dict.fromkeys(used))} para {subject}"
        if passed
        else f"mencionó {subject} pero no llamó ninguna tool de datos"
    )
    return EvalResult(
        label="pokemon_tool_used",
        passed=passed,
        reasoning=reasoning,
        metadata={
            "mentioned": mentioned,
            "tools": [t.name for t in turn.tools],
            "used_get_pokedex_entry": "get_pokedex_entry" in used,
            "narrated_opening": narrated,
        },
    )


def _single_pokedex_entry(turn: Turn) -> bool:
    """Exactly one Pokémon looked up with get_pokedex_entry (the narration protocol applies)."""
    calls = {
        json.dumps(t.args, sort_keys=True, default=str)
        for t in turn.tools
        if t.name == "get_pokedex_entry" and not t.error
    }
    return len(calls) == 1


def check_persona(turn: Turn) -> EvalResult | None:
    """(b) Dexter's voice: hard rules from the system prompt and SKILL.md."""
    reply = turn.reply.strip()
    if not reply:
        return None
    failed: list[str] = []

    words = re.findall(r"[a-záéíóúüñ]+", reply.lower())
    es = sum(w in _ES_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    ratio = es / (es + en) if es + en else None
    if es + en >= 10 and ratio < 0.8:
        failed.append("spanish")

    if "!" in reply or "¡" in reply:
        failed.append("no_exclamations")

    norm = normalize(reply)
    empathy = [p for p in _EMPATHY if re.search(rf"\b{p}\b", norm)]
    if empathy:
        failed.append("no_empathy")

    if any(("\U0001f300" <= c <= "\U0001faff" or "☀" <= c <= "➿") and c not in "♀♂" for c in reply):
        failed.append("no_emoji")

    opening = pokedex_opening(reply)
    bold_titles = [
        line
        for line in reply.splitlines()
        if _BOLD_LINE_RE.match(line) and not (opening and pokedex_opening(line.strip()))
    ]
    if _HEADING_RE.search(reply) or bold_titles:
        failed.append("no_headings")

    opening_required = _single_pokedex_entry(turn)
    if opening_required and opening is None:
        failed.append("pokedex_opening")

    return EvalResult(
        label="dexter_persona",
        passed=not failed,
        reasoning="ok" if not failed else "falló: " + ", ".join(failed),
        metadata={
            "failed": failed,
            "spanish_ratio": round(ratio, 2) if ratio is not None else None,
            "empathy": empathy,
            "opening_required": opening_required,
            "addresses_trainer": bool(_TRAINER_RE.search(reply)),
        },
    )


def check_grounding(turn: Turn) -> EvalResult | None:
    """(c) Numbers and the Pokédex opening come from tool results (or the user)."""
    opening = pokedex_opening(turn.reply)
    found = numbers(turn.reply)
    if not found and opening is None:
        return None
    ev = _collect(_turn_texts(turn))
    allowed = ev.numbers | _ALWAYS_ALLOWED
    ungrounded = list(dict.fromkeys(_fmt(n) for n in found if _key(n) not in allowed))

    mismatch = None
    if opening is not None:
        name, genus = normalize(opening.group("name")), _genus(opening.group("genus"))
        known = ev.entries.get(name)
        if known is None:
            mismatch = f"apertura de {name} sin datos de get_pokedex_entry"
        elif known != genus:
            mismatch = f"género '{genus}' en vez de '{known}'"

    passed = not ungrounded and mismatch is None
    problems = []
    if ungrounded:
        problems.append("cifras sin respaldo: " + ", ".join(map(str, ungrounded[:10])))
    if mismatch:
        problems.append(mismatch)
    return EvalResult(
        label="grounded_facts",
        passed=passed,
        reasoning="ok" if passed else "; ".join(problems),
        metadata={
            "numbers": len(found),
            "ungrounded": ungrounded[:10],
            "opening_mismatch": mismatch,
            "evidence_tools": [t.name for t in turn.tools if t.name not in SCAFFOLDING_TOOLS],
        },
    )


def evaluate_turn(turn: Turn) -> list[EvalResult]:
    """Only applicable checks are returned (rates in Datadog are over applicable spans)."""
    return [r for r in (check_tool_use(turn), check_persona(turn), check_grounding(turn)) if r]


# --- team swaps (#46) ---------------------------------------------------------------

# "saco a Pidgey", "retiraría a pidgey", "reemplaza a Pidgey", "Pidgey sale", "fuera Pidgey".
_SWAP_VERB = r"(?:sac|retir|quit|reemplaz|sustitu|remplaz|elimin|descart|cambi)\w*"
_SWAP_AFTER = r"\s+(?:sale|queda\s+fuera|se\s+va|fuera|abandona)\b"


def swap_mentions(reply: str, names: list[str]) -> set[str]:
    """Names in ``names`` that the reply announces as leaving the team. Pure."""
    norm = normalize(reply)
    out = set()
    for name in names:
        n = re.escape(normalize(name))
        # Up to two words in between: "saco a pidgey", "el candidato de retiro es togepi".
        # Never across "por": "sustituirlo por Torterra" names who comes in.
        before = rf"\b{_SWAP_VERB}(?:\s+(?!por\b)[a-z]+){{0,2}}?\s+{n}\b"
        bare = rf"\b(?:fuera|sale)\s+{n}\b"
        after = rf"\b{n}{_SWAP_AFTER}"
        if re.search(before, norm) or re.search(bare, norm) or re.search(after, norm):
            out.add(name)
    return out


def check_team_swap(reply: str, previous: list[str], current: list[str]) -> EvalResult | None:
    """The Pokémon the reply says it takes out is the one missing from the new analyze_team input.

    Applies when ``current`` is ``previous`` with exactly one member swapped. Passes when
    the reply announces that one leaving; other members may be named as discarded
    alternatives ("retirar a Mew, por el contrario, debilita…").
    """
    prev = {pokemon_name(p) for p in previous}
    cur = {pokemon_name(p) for p in current}
    removed, added = prev - cur, cur - prev
    if len(removed) != 1 or len(added) != 1:
        return None
    [out] = removed
    announced = swap_mentions(reply, sorted(prev))
    wrong = sorted(announced - removed)
    if out in announced:
        return EvalResult("team_swap_consistent", True, f"anuncia que sale {out}", {"removed": out, "announced": sorted(announced)})
    if wrong:
        reason = f"dice que sale {', '.join(wrong)}, pero el análisis sacó a {out}"
    else:
        reason = f"no anuncia que sale {out} (el que quitó del análisis)"
    return EvalResult("team_swap_consistent", False, reason, {"removed": out, "announced": sorted(announced)})


def pokemon_name(value: str) -> str:
    return normalize(value).replace(" ", "-")
