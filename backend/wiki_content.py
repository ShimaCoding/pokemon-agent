"""
Wiki Viviente — contenido educativo del modo 'learn'.

Single source of truth del contenido pedagógico que alimenta al modo learn del
frontend (diagrama de arquitectura + panel de lecciones) y al EduPanel del modo
dev. Todo el contenido está en español y se sirve como JSON vía los endpoints
/api/wiki/lessons, /api/wiki/architecture y /api/wiki/events-catalog.

Estructuras:
  - Lesson: unidad de contenido markdown con metadatos (id, título, nivel, tags,
    eventos SSE relacionados, nodos del diagrama relacionados).
  - ARCHITECTURE: nodos y aristas del diagrama de flujo del agente.
  - EVENTS_CATALOG: mapea cada tipo de evento SSE que emite _sse_generator a una
    descripción, lección y nodo relacionados. Usado para tooltips en el timeline.
"""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    level: str  # "intro" | "intermediate" | "advanced"
    tags: list[str]
    related_events: list[str]
    related_nodes: list[str]
    body_md: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Lecciones
# ---------------------------------------------------------------------------

LESSONS: list[Lesson] = [
    Lesson(
        id="agent_intro",
        title="¿Qué es un agente de IA?",
        level="intro",
        tags=["agent", "react", "loop"],
        related_events=["start", "agent_init", "llm_call", "done"],
        related_nodes=["strands_agent", "llm_router"],
        body_md="""### ¿Qué es un agente de IA?

Un **agente de IA** es un programa que combina un modelo de lenguaje con la capacidad de **decidir y ejecutar acciones** en un entorno. A diferencia de un chatbot clásico —que sólo genera texto en respuesta a un prompt— un agente mantiene un bucle en el que observa el estado, piensa, elige una herramienta, la ejecuta, lee el resultado y vuelve a decidir qué hacer. Ese bucle se conoce como **ReAct** (Reason + Act): razonar y actuar alternadamente hasta completar la tarea.

En MCPokedex, Dexter es un agente: cuando le preguntas "dame la info de Pikachu", no inventa la respuesta. Primero decide que necesita datos externos, llama a la tool `get_pokemon_info` del servidor MCP, espera el resultado, y recién entonces narra la ficha. Si el usuario pidiera comparar tipos, podría decidir llamar también a `get_type_effectiveness`. El modelo no ejecuta nada por sí mismo: solamente **elige** qué herramienta usar; quien las ejecuta es el runtime (Strands Agents SDK).

```
usuario → LLM decide → tool() → resultado → LLM decide → … → respuesta final
```

La diferencia clave con un chatbot es la **autonomía limitada**: el agente puede encadenar múltiples tool calls sin intervención humana, pero sólo dentro del catálogo de herramientas que le expusimos. El system prompt define su personalidad (Dexter sarcástico), las tools definen lo que puede hacer, y el loop del SDK orquesta todo.
""",
    ),
    Lesson(
        id="mcp_protocol",
        title="¿Qué es MCP (Model Context Protocol)?",
        level="intro",
        tags=["mcp", "protocol", "tools"],
        related_events=["agent_init", "tool_call", "tool_result"],
        related_nodes=["mcp_server", "strands_agent"],
        body_md="""### ¿Qué es MCP (Model Context Protocol)?

**MCP** es un protocolo abierto, creado por Anthropic en 2024, que estandariza cómo un agente de IA se conecta a **fuentes de contexto externas**: herramientas, recursos (archivos, DBs) y prompts reutilizables. Antes de MCP, cada integración agente↔API era artesanal; con MCP cualquier cliente (Claude Desktop, Cursor, tu agente Strands) puede hablar con cualquier servidor compatible usando el mismo protocolo.

MCP funciona sobre **JSON-RPC 2.0** y expone tres primitivas:

- **tools** — funciones que el agente puede invocar (`get_pokemon_info`, `analyze_team`).
- **resources** — datos de sólo lectura direccionables por URI (p. ej. `pokedex://025`).
- **prompts** — plantillas de prompt parametrizadas que el cliente puede pedir al servidor.

```jsonc
// Request del cliente MCP (agente)
{"jsonrpc":"2.0","id":1,"method":"tools/list"}

// Response del servidor MCP
{"jsonrpc":"2.0","id":1,"result":{"tools":[
  {"name":"get_pokemon_info","description":"...","inputSchema":{...}}
]}}
```

El **descubrimiento es dinámico**: cuando Strands arranca, llama `list_tools_sync()` sobre el MCPClient y recibe el catálogo completo del servidor. Eso significa que podemos agregar tools al servidor MCP Pokémon sin tocar ni redeployar el backend de MCPokedex: en el próximo request, el agente las verá automáticamente. Esa es la ventaja arquitectónica más grande de MCP sobre las tool calls clásicas de OpenAI.
""",
    ),
    Lesson(
        id="strands_sdk",
        title="¿Qué es Strands Agents SDK?",
        level="intermediate",
        tags=["strands", "sdk", "hooks"],
        related_events=["agent_init", "llm_call", "tool_call", "tool_result", "text"],
        related_nodes=["strands_agent"],
        body_md="""### ¿Qué es Strands Agents SDK?

**Strands Agents** es el SDK de agentes open-source de AWS que implementa el loop ReAct por ti. En lugar de escribir manualmente "mándale el prompt al LLM → parseá la respuesta → ejecutá la tool → reenviá el resultado", le pasás un modelo, una lista de tools y un system prompt, y llamás `agent.stream_async(query)`. Strands se encarga del resto.

Tres conceptos centrales:

1. **Modelos**: `LiteLLMModel`, `BedrockModel`, `AnthropicModel`, etc. En MCPokedex usamos `LiteLLMModel` + un LiteLLM Router para tener fallback multi-proveedor (Groq → OpenRouter).
2. **Event loop asíncrono**: `stream_async(query)` devuelve un async iterator que emite eventos mientras el agente piensa (`data` con deltas de texto, `result` al terminar).
3. **Hooks**: callbacks que se disparan en puntos específicos del loop —`BeforeToolCallEvent`, `AfterToolCallEvent`, `BeforeModelCallEvent`, etc. Son la forma confiable de observar lo que hace el agente sin parchear su interior.

```python
from strands import Agent
from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent

class _ToolHook:
    def register_hooks(self, registry):
        registry.add_callback(BeforeToolCallEvent, self._capture)
        registry.add_callback(AfterToolCallEvent, self._result)
    # ...

agent = Agent(model=model, tools=tools, system_prompt=SP, hooks=[_ToolHook()])
async for event in agent.stream_async(query):
    if event.get("data"): yield event["data"]      # delta de texto
    if event.get("result"): break                   # terminó
```

En MCPokedex, los hooks son cómo capturamos cada `tool_call` con sus argumentos completos y los retransmitimos al frontend vía SSE. Sin hooks tendríamos que intentar parsear el streaming crudo, que es mucho menos confiable porque el input de la tool llega como string parcial hasta que se completa el bloque.
""",
    ),
    Lesson(
        id="litellm_router",
        title="¿Qué es LiteLLM y por qué usar Router?",
        level="intermediate",
        tags=["litellm", "router", "fallback"],
        related_events=["llm_call", "model_attempt"],
        related_nodes=["llm_router"],
        body_md="""### ¿Qué es LiteLLM y por qué usar Router?

**LiteLLM** es una capa de abstracción que te permite llamar a 100+ proveedores de LLMs (OpenAI, Anthropic, Groq, Gemini, OpenRouter, Bedrock…) con la misma firma estilo OpenAI. En vez de aprender 10 SDKs distintos, importas `litellm` y llamás `litellm.completion(model="groq/qwen-...", messages=[...])`.

Encima de eso, **LiteLLM Router** agrega lógica de orquestación: load-balancing entre modelos, rate-limit handling, y —la feature que usamos nosotros— **fallback automático**. Declarás una lista priorizada de model groups y el Router va probando uno por uno hasta que alguno responda:

```python
router = Router(model_list=[
    {"model_name": "agent-model", "litellm_params": {"model": "groq/qwen/qwen3-32b", ...}},
    {"model_name": "agent-model", "litellm_params": {"model": "openrouter/google/gemma-...", ...}},
], fallbacks=[{"agent-model": ["agent-model"]}])
```

En MCPokedex esto se traduce en resiliencia real: si Groq está caído o te quedaste sin rate limit, el request reintenta transparentemente en OpenRouter. El frontend **sí** lo ve —cada intento emite un evento `model_attempt` con `status: "success" | "failed"`— pero el usuario recibe la respuesta igual.

La integración con Strands tiene un truco: `LiteLLMModel` de Strands llama `litellm.completion()` directamente, sin conocer al Router. Resolvemos eso parcheando monkeypatch `litellm.completion`/`litellm.acompletion` con las versiones del Router antes de instanciar el modelo. Un ContextVar evita el bucle infinito cuando el Router internamente llama a litellm otra vez.
""",
    ),
    Lesson(
        id="sse_streaming",
        title="¿Qué son Server-Sent Events?",
        level="intermediate",
        tags=["sse", "streaming", "http"],
        related_events=["start", "text", "done", "error"],
        related_nodes=["browser", "fastapi"],
        body_md="""### ¿Qué son Server-Sent Events (SSE)?

**Server-Sent Events** es un estándar HTTP (parte de HTML5) para que el servidor empuje eventos al navegador sobre una única conexión de larga duración. El cliente abre un `EventSource` (o un `fetch` con streaming) hacia un endpoint, y el servidor va mandando frames con el formato:

```
data: {"type":"text","delta":"Hola "}

data: {"type":"text","delta":"entrenador"}

data: {"type":"done","elapsed_ms":1234}

```

Cada frame termina con doble `\\n`. Es texto plano, unidireccional (servidor → cliente) y viaja sobre HTTP/1.1 o HTTP/2 estándar. En FastAPI lo servimos con `StreamingResponse(generator, media_type="text/event-stream")`.

**¿Por qué SSE y no WebSocket?** Porque nuestro tráfico es unidireccional: el cliente manda **una** query y recibe **muchos** eventos; no hay mensajes del cliente al servidor durante el stream. WebSocket sería sobre-ingeniería: obligaría a mantener una conexión full-duplex, pasar por una negociación de upgrade, y lidiar con reconnection manual. SSE tiene reconnection automática por diseño (`Last-Event-ID`), funciona sobre cualquier proxy HTTP (Traefik, Nginx) sin configuración especial, y se debuggea con `curl -N`.

En MCPokedex, el endpoint `/api/agent/run` devuelve una sola SSE response por request con todos los eventos del loop: `start` → `agent_init` → `llm_call` → `model_attempt` → `tool_call` → `tool_result` → muchos `text` → `done`. El frontend los parsea incrementalmente y actualiza la UI en tiempo real.
""",
    ),
    Lesson(
        id="tool_call_anatomy",
        title="Anatomía de un tool call",
        level="intermediate",
        tags=["tools", "llm", "mcp"],
        related_events=["tool_call", "tool_result"],
        related_nodes=["strands_agent", "llm_router", "mcp_server"],
        body_md="""### Anatomía de un tool call paso a paso

Un **tool call** es el momento en que el LLM decide que no puede responder solo y necesita ejecutar una función externa. Parece un único paso pero son **cuatro actores** colaborando:

1. **El LLM decide** — Recibe el prompt + el catálogo de tools (nombre, descripción, JSON schema de inputs) y su respuesta incluye un bloque estructurado: `{"name": "get_pokemon_info", "input": {"name": "pikachu"}, "id": "toolu_01..."}`. El modelo **no ejecuta** la tool, sólo la nombra.

2. **Strands intercepta** — El SDK detecta ese bloque en la respuesta del LLM, dispara `BeforeToolCallEvent` (aquí nuestro hook captura name + input), y busca la implementación en el registro de tools que le pasamos al crear el `Agent`.

3. **El MCPClient ejecuta** — Como la tool viene del servidor MCP remoto, Strands la delega al MCPClient, que envía un request JSON-RPC `tools/call` al servidor vía HTTP streamable. El servidor corre la función, consulta su caché Redis o la PokeAPI, y devuelve el resultado.

4. **El LLM lee y decide de nuevo** — Strands toma el resultado, lo empaqueta como un mensaje `tool_result` con el mismo `toolUseId`, y lo agrega al historial de la conversación. Dispara `AfterToolCallEvent` (nuestro hook captura el resultado) y hace una **segunda llamada al LLM** con el historial extendido. El modelo decide: ¿respondo ahora o llamo otra tool?

```
LLM → "quiero get_pokemon_info(name=pikachu)"
         ↓ (Strands + hook BeforeToolCall)
       MCPClient → JSON-RPC tools/call → MCP Server → PokeAPI
                                              ↓
       MCPClient ← JSON-RPC response ← MCP Server
         ↓ (hook AfterToolCall)
LLM ← "aquí están los datos de pikachu: {...}"
         ↓
LLM → "Un roedor eléctrico mediocre. Tipo: Electric..."
```

Por eso en la traza ves siempre el par: un `tool_call` seguido —a veces cientos de ms después— de su `tool_result` con el mismo índice. La latencia que aparece es real: red + ejecución + lectura del siguiente token.
""",
    ),
    Lesson(
        id="reading_traces",
        title="Cómo leer la traza de eventos",
        level="intro",
        tags=["trace", "events", "debugging"],
        related_events=["start", "agent_init", "llm_call", "model_attempt", "tool_call", "tool_result", "text", "done", "error"],
        related_nodes=["browser", "fastapi", "strands_agent", "llm_router", "mcp_server"],
        body_md="""### Cómo leer la traza de eventos

Cada request a `/api/agent/run` produce una secuencia de eventos SSE que cuentan la historia del agente de principio a fin. Aprender a leerlos es la forma más rápida de entender —y debuggear— cualquier agente. Este es el orden típico:

- **`start`** — El servidor aceptó el request y seleccionó un proveedor LLM. El agente todavía no arrancó; sólo confirmamos que llegamos.
- **`agent_init`** — Strands se conectó al servidor MCP y descubrió las tools disponibles. Aquí ves cuántas tools tiene el agente y si se cargó alguna skill. Si algo anda mal con MCP, este evento revela problemas temprano.
- **`llm_call`** — Se envía el prompt al LLM (con el historial completo). El campo `call_index` te dice cuántas veces vamos: un agente puede tener varias llamadas al LLM en un mismo run (una por cada "turno de razonamiento" entre tool calls).
- **`model_attempt`** — Dentro del LiteLLM Router, se está probando un modelo concreto. `status: "success"` = respondió, `status: "failed"` = el Router va a pasar al siguiente. Aquí ves el fallback multi-proveedor funcionando.
- **`tool_call`** — El LLM decidió llamar una tool. Los `args` son el input exacto que el modelo produjo. El `index` identifica este call para matchearlo con su resultado.
- **`tool_result`** — La tool terminó y el resultado ya se le entregó al LLM. Mismo `index` que el tool_call correspondiente.
- **`text`** — Deltas de la respuesta final en streaming (un token o pocos tokens por evento). Los vas concatenando en la UI.
- **`done`** — Terminó el run. Incluye métricas: tools usadas, tokens de entrada/salida, modelos intentados, tiempo total.
- **`error`** — Algo falló irreversiblemente. Incluye un mensaje y si hay proveedor de fallback disponible.

Patrones comunes:
- **Sin tool calls**: `start` → `agent_init` → `llm_call` → `model_attempt` → muchos `text` → `done`. El LLM respondió directo porque no necesitó datos externos.
- **Un tool call**: `llm_call` → `tool_call` → `tool_result` → `llm_call` → `text`s → `done`. Dos llamadas al LLM: una para decidir la tool, otra para narrar con el resultado.
- **Fallback de modelo**: `model_attempt(failed)` → `model_attempt(success)` dentro del mismo `llm_call`. El Router probó el modelo 1, falló, saltó al 2.

Mirá el `timestamp_ms` de cada evento: es el offset en milisegundos desde el inicio del request. Eso te revela dónde se va el tiempo (típicamente: red al LLM, ejecución de tools, y segunda llamada al LLM para narrar).
""",
    ),
]


# ---------------------------------------------------------------------------
# Arquitectura (diagrama de flujo)
# ---------------------------------------------------------------------------

ARCHITECTURE: dict = {
    "nodes": [
        {
            "id": "browser",
            "label": "Browser",
            "description": (
                "El frontend React 19 del usuario. Abre una conexión SSE contra "
                "FastAPI, envía la query y renderiza los eventos en tiempo real: "
                "chat, traza, y (en modo learn) diagrama animado."
            ),
            "related_lesson_id": "sse_streaming",
            "position": {"x": 60, "y": 160},
        },
        {
            "id": "fastapi",
            "label": "FastAPI",
            "description": (
                "Backend HTTP que recibe el POST a /api/agent/run, aplica rate "
                "limiting y API-key auth, orquesta el _sse_generator y retransmite "
                "cada evento al cliente como frame SSE."
            ),
            "related_lesson_id": "sse_streaming",
            "position": {"x": 240, "y": 160},
        },
        {
            "id": "strands_agent",
            "label": "Strands Agent",
            "description": (
                "El runtime del agente. Corre el loop ReAct: pide tokens al LLM, "
                "detecta tool calls, los ejecuta vía MCPClient, alimenta los "
                "resultados de vuelta al LLM y emite eventos via hooks."
            ),
            "related_lesson_id": "strands_sdk",
            "position": {"x": 440, "y": 160},
        },
        {
            "id": "llm_router",
            "label": "LiteLLM Router",
            "description": (
                "Capa de routing de LLMs. Recibe los prompts que emite Strands y "
                "los distribuye entre proveedores (Groq → OpenRouter) con "
                "fallback automático si uno falla o rate-limitea."
            ),
            "related_lesson_id": "litellm_router",
            "position": {"x": 640, "y": 80},
        },
        {
            "id": "mcp_server",
            "label": "MCP Server",
            "description": (
                "Servidor remoto mcp-pokemon.shimadev.xyz que expone tools Pokémon "
                "(get_pokemon_info, analyze_team, …) vía JSON-RPC sobre HTTP "
                "streamable. Cachea respuestas de PokeAPI en Redis."
            ),
            "related_lesson_id": "mcp_protocol",
            "position": {"x": 640, "y": 240},
        },
    ],
    "edges": [
        {"from": "browser", "to": "fastapi", "label": "POST /api/agent/run (SSE)"},
        {"from": "fastapi", "to": "browser", "label": "event stream"},
        {"from": "fastapi", "to": "strands_agent", "label": "stream_async(query)"},
        {"from": "strands_agent", "to": "llm_router", "label": "litellm.completion()"},
        {"from": "llm_router", "to": "strands_agent", "label": "tokens + tool calls"},
        {"from": "strands_agent", "to": "mcp_server", "label": "tools/call (JSON-RPC)"},
        {"from": "mcp_server", "to": "strands_agent", "label": "tool result"},
    ],
}


# ---------------------------------------------------------------------------
# Catálogo de eventos SSE
# ---------------------------------------------------------------------------

EVENTS_CATALOG: dict = {
    "start": {
        "description_md": (
            "**start** — el servidor aceptó el request y eligió un proveedor LLM. "
            "Todavía no se contactó ni al MCP ni al LLM."
        ),
        "related_lesson_id": "reading_traces",
        "related_node_id": "fastapi",
    },
    "agent_init": {
        "description_md": (
            "**agent_init** — Strands se conectó al servidor MCP, descubrió las "
            "tools y (si corresponde) cargó la skill Dexter. A partir de aquí el "
            "agente está listo para razonar."
        ),
        "related_lesson_id": "mcp_protocol",
        "related_node_id": "mcp_server",
    },
    "llm_call": {
        "description_md": (
            "**llm_call** — Strands envía un prompt al LLM. `call_index` indica "
            "cuántas veces vamos: un mismo run suele tener 2+ llamadas (una para "
            "decidir la tool, otra para narrar con el resultado)."
        ),
        "related_lesson_id": "strands_sdk",
        "related_node_id": "strands_agent",
    },
    "model_attempt": {
        "description_md": (
            "**model_attempt** — el LiteLLM Router está probando un modelo "
            "específico. `status=success` = respondió; `status=failed` = el "
            "Router pasa al siguiente del fallback."
        ),
        "related_lesson_id": "litellm_router",
        "related_node_id": "llm_router",
    },
    "tool_call": {
        "description_md": (
            "**tool_call** — el LLM decidió invocar una tool. Los `args` son el "
            "input exacto que produjo el modelo; `index` identifica este call "
            "para matchearlo con su resultado."
        ),
        "related_lesson_id": "tool_call_anatomy",
        "related_node_id": "mcp_server",
    },
    "tool_result": {
        "description_md": (
            "**tool_result** — la tool terminó y el resultado ya se le entregó "
            "al LLM. Mismo `index` que el tool_call correspondiente."
        ),
        "related_lesson_id": "tool_call_anatomy",
        "related_node_id": "mcp_server",
    },
    "text": {
        "description_md": (
            "**text** — delta (token o pocos tokens) de la respuesta final en "
            "streaming. El frontend los concatena para mostrar la respuesta "
            "progresivamente."
        ),
        "related_lesson_id": "sse_streaming",
        "related_node_id": "browser",
    },
    "done": {
        "description_md": (
            "**done** — terminó el run. Incluye métricas: tools usadas, tokens "
            "de entrada/salida, modelos intentados, tiempo total."
        ),
        "related_lesson_id": "reading_traces",
        "related_node_id": "fastapi",
    },
    "error": {
        "description_md": (
            "**error** — algo falló irreversiblemente. Incluye mensaje y un flag "
            "`fallback_available` que indica si hay otro proveedor para reintentar."
        ),
        "related_lesson_id": "reading_traces",
        "related_node_id": "fastapi",
    },
}


# ---------------------------------------------------------------------------
# Helpers de serialización (usados por los endpoints)
# ---------------------------------------------------------------------------


def lessons_as_dicts() -> list[dict]:
    return [lesson.to_dict() for lesson in LESSONS]
