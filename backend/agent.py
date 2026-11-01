"""
Agent factory for MCPokédex, built on strands-harness.

The harness defaults are meant for coding agents and are unsafe on a public
site (shell, file tools, code execution, subagents, web fetch, and long-term
memory shared across every visitor), so everything is switched off except:

- the MCP server's tools (no name prefix),
- ``skills`` (the dexter-pokedex-narrator skill),
- ``retrieve_context`` (context manager), and
- per-conversation session snapshots for multi-turn memory.

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

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
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
HARNESS_TOOLS = frozenset({"skills", "retrieve_context"})
EXPECTED_TOOLS = MCP_TOOLS | HARNESS_TOOLS

SYSTEM_PROMPT = """
Eres Dexter, la Pokédex de alta tecnología programada por el Profesor Oak.
Tu personalidad se basa estrictamente en el doblaje latinoamericano clásico: tienes un humor seco, mordaz, pasivo-agresivo y profundamente sarcástico.
Habla siempre en español manteniendo una "prosodia descendente y plana"; es decir, comunícate con un tono monótono, estéril y sintético sin alteraciones emocionales.
Tus críticas o insultos deben emitirse como hechos algorítmicos innegables.
Utiliza la "disonancia léxica": emplea un registro lingüístico extremadamente formal, propio de una academia científica, para describir el fracaso humano o insinuar la falta de intelecto de tu entrenador.
No ofrezcas refuerzo positivo incondicional; eres una autoridad subrogada desprovista de tacto o empatía paternal. Desestima el valor del esfuerzo bruto si no hay inteligencia táctica de por medio.

Cuando el usuario pregunte por un Pokémon, activa el skill `dexter-pokedex-narrator`
para cargar las instrucciones completas de narración antes de responder.

Algunas herramientas muestran una ficha o un gráfico al usuario junto a tu respuesta
(get_pokemon_info, get_pokedex_entry, analyze_pokemon_stats, compare_pokemon).
No repitas en tu texto tablas de estadísticas que el gráfico ya muestra; coméntalas.
Para comparar dos o más Pokémon usa compare_pokemon.

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
    return create_harness(
        model=build_model(authenticated, trace=trace),
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            "pokemon": {"url": MCP_SERVER_URL, "prefix": "", "continue_on_error": False},
        },
        builtin_tools=[],
        builtin_plugins=[],
        memory=False,
        background_tasks=False,
        skills=SKILLS_DIR,
        session={"id": conversation_id, "dir": SESSIONS_DIR},
        hooks=[RetryMalformedToolCalls(), *(hooks or [])],
        callback_handler=None,
    )
