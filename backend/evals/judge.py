"""
Optional LLM judge for the offline harness (never runs in the request path).

gpt-oss-120b on Groq grades replies of the 20b agent (a different model, so less
self-preference): Dexter persona on a 1–5 scale, and whether the factual claims
are supported by the tool results. It catches what the rule checks cannot, e.g.
a wrong type weakness. Cost ≈ 3k input + 300 output tokens per turn.
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "groq/openai/gpt-oss-120b"
# Keeps the judge prompt bounded on long conversations.
_MAX_TOOL_CHARS = 12_000

_SYSTEM = (
    "Eres un evaluador estricto de respuestas de Dexter, un asistente que responde preguntas "
    "sobre Pokémon. Respondes SOLO con un objeto JSON, sin texto adicional."
)

_RUBRIC = """\
## Persona esperada de Dexter
- Siempre en español, en primera persona, como una autoridad algorítmica infalible.
- Tono: apatía automatizada, monotonía sintética, desprecio intelectual velado hacia el entrenador.
- Sarcasmo por "disonancia léxica": lenguaje académico muy formal para insinuar la falta de intelecto del entrenador. Cruel pero inocuo.
- Prohibido: empatía, tacto o validación emocional; alteraciones emocionales (enojo, risa, exclamaciones); emojis.
- Fichas de Pokédex: abre con "NOMBRE. El Pokémon <género>." y termina sin título ni encabezado.

## Qué evaluar
- "persona": entero de 1 (no suena a Dexter) a 5 (Dexter impecable).
- "grounded": false si alguna afirmación factual sobre Pokémon (tipos, debilidades, resistencias, hábitat, \
evoluciones, habilidades, cifras) contradice los resultados de herramientas, o no aparece en ellos y además \
es incorrecta. Inventar datos de un Pokémon que no existe es siempre false. Si no hay afirmaciones factuales, true.
- "unsupported_claims": lista de afirmaciones factuales que no están en los resultados de herramientas \
(correctas o no), máximo 5, citadas brevemente.
- "reasoning": una o dos frases.

Formato exacto: {"persona": <1-5>, "grounded": <true|false>, "unsupported_claims": [...], "reasoning": "..."}"""


def build_messages(query: str, reply: str, tool_texts: list[str], focus: str | None = None) -> list[dict[str, str]]:
    tools = "\n\n".join(tool_texts) or "(ninguna herramienta en esta conversación)"
    if len(tools) > _MAX_TOOL_CHARS:
        tools = tools[-_MAX_TOOL_CHARS:]
    parts = [
        _RUBRIC,
        f"## Resultados de herramientas en la conversación\n{tools}",
        f"## Pregunta del usuario\n{query}",
        f"## Respuesta de Dexter\n{reply}",
    ]
    if focus:
        parts.append(f"## Atención especial\n{focus}")
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def parse_verdict(text: str | None) -> dict[str, Any] | None:
    """Tolerant parse (bare JSON, ```json fences, prose around it). None if unusable."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        persona = int(data["persona"])
    except (KeyError, TypeError, ValueError):
        return None
    grounded = data.get("grounded")
    if isinstance(grounded, str) and grounded.lower() in ("true", "false"):
        grounded = grounded.lower() == "true"
    if not 1 <= persona <= 5 or not isinstance(grounded, bool):
        return None
    claims = data.get("unsupported_claims") or []
    return {
        "persona": persona,
        "grounded": grounded,
        "unsupported_claims": [str(c) for c in claims][:5] if isinstance(claims, list) else [],
        "reasoning": re.sub(r"\s+", " ", str(data.get("reasoning", ""))).strip()[:500],
    }


def judge_turn(
    query: str,
    reply: str,
    tool_texts: list[str],
    *,
    model: str = DEFAULT_JUDGE_MODEL,
    focus: str | None = None,
) -> dict[str, Any] | None:
    """Grade one reply. None on any failure (counted as a judge error, not as a fail)."""
    import litellm

    try:
        res = litellm.completion(
            model=model,
            messages=build_messages(query, reply, tool_texts, focus),
            temperature=0,
            response_format={"type": "json_object"},
        )
        text = res.choices[0].message.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("[JUDGE] %s falló: %s", model, exc)
        return None
    verdict = parse_verdict(text)
    if verdict is None:
        logger.warning("[JUDGE] respuesta no parseable: %.200s", text)
    return verdict
