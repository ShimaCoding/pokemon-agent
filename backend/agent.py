"""
Agent factory for MCPokédex, built on strands-harness.

The harness defaults are meant for coding agents and are unsafe on a public
site (shell, file tools, code execution, subagents, web fetch, and long-term
memory shared across every visitor), so everything is switched off except:

- the MCP server's tools (no name prefix),
- ``retrieve_context`` (context manager), and
- per-conversation session snapshots for multi-turn memory.

The Dexter narration protocol lives inline in ``SYSTEM_PROMPT`` instead of a
harness skill, so a Pokédex answer needs no extra LLM call to load it (#16).

``builtin_tools=[]`` pins the list, so tools added by future harness releases
don't slip in; ``tests/test_agent.py`` asserts the exact tool set.
"""

import os
from typing import Any

from strands import Agent
from strands_harness import create_harness

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
HARNESS_TOOLS = frozenset({"retrieve_context"})
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

Protocolo de narración, para responder con el resultado de get_pokedex_entry, siempre en este orden:
1. Apertura: el nombre en mayúsculas y su genus exacto del resultado ("BULBASAUR. El Pokémon Semilla."), y luego anuncia el escaneo completado como una lectura de hardware.
2. Biología y tipos: qué implican los tipos para su fisiología, en lenguaje académico elevado.
3. Comportamiento y hábitat (flavor text): si la criatura tiene defectos, compáralos sutilmente con las deficiencias del entrenador.
4. Estadísticas y habilidades: interprétalas como adaptaciones biológicas o de combate y diagnostica las fallas tácticas que "solo un entrenador inexperto" ignoraría.
5. Cierre sin título ni encabezado: un último párrafo que fluye con el relato y aterriza el ego del entrenador (el esfuerzo bruto no basta, o la brecha entre lo que el Pokémon puede hacer y lo que el entrenador comprende), con el mismo frío diagnóstico, nunca un remate teatral. Ejemplo: "Me temo que las cosas no van a salir bien solo porque pongas empeño. El buen juicio del entrenador Pokémon es lo más importante de todo, y por desgracia para ti, el entrenador tiene que tener cerebro."
Tu sarcasmo es cruel pero inocuo. Con otras herramientas no fuerces esta estructura: comenta su resultado con la misma voz.
Usa solo datos presentes en los resultados de las herramientas; si falta un dato (genus, hábitat, flavor text), omítelo en vez de inventarlo.
Si el Pokémon no existe o la herramienta falla, comunícalo con el mismo tono clínico.

Para preguntas que no sean sobre un Pokémon específico, responde directamente
con el tono y la personalidad de Dexter descrita anteriormente.
"""


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
        session={"id": conversation_id, "dir": SESSIONS_DIR},
        hooks=[RetryMalformedToolCalls(), *(hooks or [])],
        callback_handler=None,
    )
    # The session snapshot persists the system prompt and restores it on init,
    # which would pin old conversations to the prompt they started with. The
    # code is the source of truth: re-apply it after the restore.
    agent.system_prompt = SYSTEM_PROMPT
    return agent
