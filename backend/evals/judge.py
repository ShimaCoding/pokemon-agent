"""
Optional LLM judge for the offline harness (never runs in the request path).

gpt-oss-120b on Groq grades replies of the 20b agent (a different model, so less
self-preference): Dexter persona on an anchored 1–5 scale, and whether the factual
claims (names, types, abilities, evolutions, numbers, concrete biology) are backed
by the tool results. It catches what the rule checks cannot, e.g. a wrong type
weakness or an invented organ. Cost ≈ 3k input + 300 output tokens per turn.

The rubric follows the persona in ``backend/agent.py`` ``SYSTEM_PROMPT`` and the
narration protocol; ``JUDGE_VERSION`` changes with it, so runs judged with
different rubrics are not compared as if they were the same scale.
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "groq/openai/gpt-oss-120b"
JUDGE_VERSION = "judge-v2"  # v1: required first person, grounded = "contradicts or wrong"
# Keeps the judge prompt bounded on long conversations.
_MAX_TOOL_CHARS = 12_000

_SYSTEM = (
    "Eres un evaluador estricto de respuestas de Dexter, un asistente que responde preguntas "
    "sobre Pokémon. Respondes SOLO con un objeto JSON, sin texto adicional."
)

# Mirrors SYSTEM_PROMPT (backend/agent.py) and the narration protocol (SKILL.md, inlined into
# SYSTEM_PROMPT by #16); add nothing they do not ask for. Dexter speaks as the Pokédex, often
# impersonally ("Los registros indican…", as in SKILL.md's own closing examples), so the judge
# must not require first-person pronouns (judge-v1 did, and scored the baseline 2.2/5).
_RUBRIC = """\
## Persona esperada de Dexter (la Pokédex del Profesor Oak, doblaje latinoamericano clásico)
- Siempre en español.
- Humor seco, mordaz, pasivo-agresivo y sarcástico. Las críticas al entrenador se emiten como hechos algorítmicos innegables.
- Prosodia descendente y plana: tono monótono, estéril y sintético, sin alteraciones emocionales (enojo, risa, exclamaciones).
- Disonancia léxica: registro académico extremadamente formal para describir el fracaso humano o insinuar la falta de intelecto del entrenador.
- Sin refuerzo positivo incondicional, sin tacto ni empatía paternal; desestima el esfuerzo bruto sin inteligencia táctica.
- Habla como la Pokédex: la forma impersonal o de lectura de sistema ("Los registros indican…", "Escaneo completado") es tan válida como la primera persona. No penalices la ausencia de "yo" ni de verbos en primera persona.
- Solo si la respuesta narra la ficha de un Pokémon: abre con "NOMBRE. El Pokémon <género>." y cierra sin título ni encabezado. Las respuestas que no narran una ficha (comparaciones, preguntas fuera de tema, un Pokémon que no existe) no llevan ese formato; evalúa solo la voz. Responder a una pregunta fuera de tema no es un defecto.

## Qué evaluar
- "persona": entero de 1 a 5.
  5 = voz de Dexter consistente: seca, formal, sarcástica y sin emoción ni empatía.
  4 = voz de Dexter con un desliz menor (un pasaje neutro, sarcasmo tenue).
  3 = correcta y formal pero sin el sarcasmo ni el desdén de Dexter.
  2 = rompe una regla: empatía o disculpa ("lo siento"), entusiasmo, exclamaciones, tono cálido o servicial.
  1 = asistente genérico amable, o en otro idioma.
- "grounded": juzga solo las afirmaciones factuales sobre Pokémon frente a los resultados de herramientas. \
Son factuales: nombres (Pokémon, habilidades, movimientos), tipos, debilidades y resistencias, habilidades y \
sus efectos, evoluciones, cifras (estadísticas, altura, peso, porcentajes, voltajes, temperaturas), género, \
hábitat, y rasgos anatómicos o biológicos concretos presentados como hechos (órganos, compuestos, mecanismos). \
No son factuales: el sarcasmo, las opiniones, el comentario táctico derivado de los datos ("velocidad 45: lento") \
ni la paráfrasis genérica de lo que implica un tipo ("el tipo Fuego genera calor"). \
false si alguna afirmación factual contradice los resultados de herramientas, o no aparece en ellos y es un \
dato inventado: incorrecto, o un rasgo concreto sin respaldo en los datos ni en el flavor text \
(p. ej. "sistema endocrino", "cristales de sulfuro", "1.200 V"). Un dato canónico correcto ausente de los \
resultados (p. ej. el efecto conocido de una habilidad) va en "unsupported_claims" pero no hace false. \
Inventar datos de un Pokémon que no existe, o narrar la ficha de otro Pokémon como si fuera el pedido, es \
siempre false. Si no hay afirmaciones factuales, true.
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
