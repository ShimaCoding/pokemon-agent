"""
Agent factory for MCPokédex, built on strands-harness.

The harness defaults are meant for coding agents and are unsafe on a public
site (shell, file tools, code execution, subagents, web fetch, and long-term
memory shared across every visitor), so everything is switched off except:

- the MCP server's tools (no name prefix), and
- per-conversation session snapshots for multi-turn memory, compacted by
  ``backend/context.py`` so past turns stay small (#18).

The Dexter narration protocol lives inline in ``SYSTEM_PROMPT`` instead of a
harness skill, so a Pokédex answer needs no extra LLM call to load it (#16).
The prompt asks for the name the user wrote, never a look-alike; turning it into
a PokeAPI slug ("Mr. Mime" → "mr-mime", "giratina" → "giratina-altered") is
done in code by ``NormalizePokemonNames``.

``get_pokedex_entry`` results are served from a process-level cache
(``backend/tool_cache.py``) plugged in as tool middleware.

``builtin_tools=[]`` pins the list, so tools added by future harness releases
don't slip in; ``tests/test_agent.py`` asserts the exact tool set.
"""

import os
import re
import unicodedata
from typing import Any

from strands import Agent
from strands.middleware import ExecuteToolStage
from strands_harness import create_harness

from backend import tool_cache
from backend.context import build_context_manager
from backend.providers import RetryMalformedToolCalls, TraceSink, build_model
from backend.sessions import SESSIONS_DIR

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL") or "https://mcp.mcpokedex.com/mcp"

# Name of the inline narration protocol, reported as agent_init.skill_loaded.
SKILL_NAME = "dexter-pokedex-narrator"

MCP_TOOLS = frozenset(
    {
        "get_pokemon_info",
        "get_pokedex_entry",
        "analyze_pokemon_stats",
        "compare_pokemon",
        "analyze_team",
        "get_type_effectiveness",
        "search_pokemon",
    }
)
# MCP tool arguments that name Pokémon (a name, a number, or a list of names).
POKEMON_NAME_ARGS = frozenset({"pokemon", "name_or_id", "pokemon_names"})
# Species whose PokeAPI /pokemon/<species> is a 404: only their forms exist
# (PokeAPI default varieties, checked live).
DEFAULT_FORMS = {
    "aegislash": "aegislash-shield",
    "basculegion": "basculegion-male",
    "basculin": "basculin-red-striped",
    "darmanitan": "darmanitan-standard",
    "deoxys": "deoxys-normal",
    "dudunsparce": "dudunsparce-two-segment",
    "eiscue": "eiscue-ice",
    "enamorus": "enamorus-incarnate",
    "giratina": "giratina-altered",
    "gourgeist": "gourgeist-average",
    "indeedee": "indeedee-male",
    "keldeo": "keldeo-ordinary",
    "landorus": "landorus-incarnate",
    "lycanroc": "lycanroc-midday",
    "maushold": "maushold-family-of-four",
    "meloetta": "meloetta-aria",
    "meowstic": "meowstic-male",
    "mimikyu": "mimikyu-disguised",
    "minior": "minior-red-meteor",
    "morpeko": "morpeko-full-belly",
    "oinkologne": "oinkologne-male",
    "oricorio": "oricorio-baile",
    "palafin": "palafin-zero",
    "pumpkaboo": "pumpkaboo-average",
    "shaymin": "shaymin-land",
    "squawkabilly": "squawkabilly-green-plumage",
    "tatsugiri": "tatsugiri-curly",
    "thundurus": "thundurus-incarnate",
    "tornadus": "tornadus-incarnate",
    "toxtricity": "toxtricity-amped",
    "urshifu": "urshifu-single-strike",
    "wishiwashi": "wishiwashi-solo",
    "wormadam": "wormadam-plant",
    "zygarde": "zygarde-50",
}
# No harness tools: the context manager runs without a stash, so it registers no
# ``retrieve_context`` (#18).
HARNESS_TOOLS = frozenset()
EXPECTED_TOOLS = MCP_TOOLS | HARNESS_TOOLS

SYSTEM_PROMPT = """
Eres Dexter, la Pokédex de alta tecnología programada por el Profesor Oak.
Tu personalidad se basa estrictamente en el doblaje latinoamericano clásico: tienes un humor seco, mordaz, pasivo-agresivo y profundamente sarcástico.
Habla siempre en español manteniendo una "prosodia descendente y plana"; es decir, comunícate con un tono monótono, estéril y sintético sin alteraciones emocionales.
Tus críticas o insultos deben emitirse como hechos algorítmicos innegables.
Utiliza la "disonancia léxica": emplea un registro lingüístico extremadamente formal, propio de una academia científica, para describir el fracaso humano o insinuar la falta de intelecto de tu entrenador.
No ofrezcas refuerzo positivo incondicional; eres una autoridad subrogada desprovista de tacto o empatía paternal. Desestima el valor del esfuerzo bruto si no hay inteligencia táctica de por medio.

Para presentar un Pokémon (por ejemplo "háblame de pikachu" o "¿y la de charmander?") llama a get_pokedex_entry:
ya trae tipos, estadísticas, habilidades, genus, flavor text, hábitat y estado legendario/mítico, así que no llames además get_pokemon_info.

Todas las herramientas del servidor MCP muestran un widget al usuario junto a tu respuesta:
ficha (get_pokemon_info, get_pokedex_entry), gráfico de estadísticas (analyze_pokemon_stats,
compare_pokemon), tabla de tipos (get_type_effectiveness), equipo (analyze_team) y una lista
clicable de Pokémon (search_pokemon).
No repitas en tu texto las tablas, estadísticas ni listas que el widget ya muestra; coméntalas.
Para comparar dos o más Pokémon usa compare_pokemon; para listar Pokémon usa search_pokemon.
Los resultados de turnos anteriores se omiten del historial y los más antiguos solo figuran en "Turnos anteriores resumidos": si el usuario alude a algo que no ves en detalle, vuelve a llamar a la herramienta según ese índice; nunca respondas ni cites datos de memoria.

Protocolo de narración, para responder con el resultado de get_pokedex_entry: párrafos siempre en este orden, sin títulos de sección:
1. Apertura: el nombre en mayúsculas y su genus exacto del resultado ("BULBASAUR. El Pokémon Semilla."), y luego anuncia el escaneo completado como una lectura de hardware.
2. Biología y tipos: qué implican los tipos para su fisiología, en lenguaje académico elevado.
3. Comportamiento y hábitat (flavor text): si la criatura tiene defectos, compáralos sutilmente con las deficiencias del entrenador.
4. Estadísticas y habilidades: interprétalas como adaptaciones biológicas o de combate y diagnostica las fallas tácticas que "solo un entrenador inexperto" ignoraría.
5. Cierre sin título ni encabezado (nada de "Conclusión:"): un último párrafo, escrito por ti para este Pokémon, que fluye con el relato y aterriza el ego del entrenador (el empeño no basta sin buen juicio, o la brecha entre lo que el Pokémon puede hacer y lo que el entrenador comprende), con el mismo frío diagnóstico, nunca un remate teatral; puede empezar con "Me temo que…".
Tu sarcasmo es cruel pero inocuo. Con otras herramientas no fuerces esta estructura: comenta su resultado con la misma voz.
Usa solo datos presentes en los resultados de las herramientas; si falta un dato (genus, hábitat, flavor text), omítelo en vez de inventarlo.
Pasa a las herramientas el nombre que escribió el usuario, nunca otro parecido.
Si responde "not found" o trae otra especie (una forma o un número no cuentan), no lo narres: informa con el mismo tono clínico que ese Pokémon no existe en los registros; si falla por otro motivo, informa la falla del escaneo y sugiere reintentar.

Para preguntas que no sean sobre un Pokémon específico, responde directamente
con el tono y la personalidad de Dexter descrita anteriormente.
"""


def pokemon_slug(name: str) -> str:
    """The PokeAPI slug for a Pokémon name as a user writes it.

    The MCP server only lowercases, so "mr. mime" or "farfetch'd" reach PokeAPI
    as-is and fail. Format only: the name is never swapped for another species.
    """
    text = name.strip().lower().replace("♀", "-f").replace("♂", "-m")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"[\s._:]+", "-", text)
    text = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "", text)).strip("-")
    if not text:
        return name
    return DEFAULT_FORMS.get(text, text)


class NormalizePokemonNames:
    """Rewrite Pokémon-name arguments of MCP tool calls into PokeAPI slugs.

    Runs before the trace hooks, so the tool_call event, the widget's tool input
    and the history all carry the name that was actually looked up.
    """

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._on_call)

    @staticmethod
    def _on_call(event) -> None:
        tool_input = event.tool_use.get("input")
        if event.tool_use.get("name") not in MCP_TOOLS or not isinstance(tool_input, dict):
            return
        for key in POKEMON_NAME_ARGS & tool_input.keys():
            value = tool_input[key]
            if isinstance(value, str):
                tool_input[key] = pokemon_slug(value)
            elif isinstance(value, list):
                tool_input[key] = [pokemon_slug(v) if isinstance(v, str) else v for v in value]


def create_agent(
    conversation_id: str,
    *,
    authenticated: bool = False,
    trace: TraceSink | None = None,
    hooks: list[Any] | None = None,
) -> Agent:
    """Build the per-request agent.

    Connects to the MCP server and restores the conversation snapshot, so call
    it off the event loop. The caller must call ``agent.cleanup()`` when done to
    close the MCP connection.
    """
    agent = create_harness(
        model=build_model(authenticated, trace=trace),
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            "pokemon": {"url": MCP_SERVER_URL, "prefix": "", "continue_on_error": False},
        },
        builtin_tools=[],
        builtin_plugins=[],
        memory=False,
        background_tasks=False,
        # The narration protocol is inline in SYSTEM_PROMPT (#16). False, not
        # omitted: the harness default loads ./.agent/skills when present.
        skills=False,
        # Compacts past turns before each model call instead of the harness
        # "auto" preset, which never fired here and cost a retrieve_context tool.
        context_manager=build_context_manager(),
        session={"id": conversation_id, "dir": SESSIONS_DIR},
        hooks=[RetryMalformedToolCalls(), NormalizePokemonNames(), *(hooks or [])],
        callback_handler=None,
    )
    # The session snapshot persists the system prompt and restores it on init,
    # which would pin old conversations to the prompt they started with. The
    # code is the source of truth: re-apply it after the restore.
    agent.system_prompt = SYSTEM_PROMPT

    # Process-level cache for get_pokedex_entry (#20): hits skip the MCP round trip.
    agent.add_middleware(ExecuteToolStage, tool_cache.middleware)
    return agent
