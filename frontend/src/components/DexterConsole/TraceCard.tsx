import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import type {
  LlmCallEvent,
  ModelAttemptEvent,
  SystemLogEvent,
  ToolCallEvent,
  ToolResultEvent,
  TraceEvent,
} from '../../types'
import { guessToolType } from '../../hooks/useAgentStream'
import { JsonViewer, tryParseJson } from './JsonViewer'
import styles from './TraceCard.module.css'

// ── Educational content ───────────────────────────────────────────

const DOCS = {
  agent: `
### ¿Cómo funciona el Agente?

El agente **Dexter** es una aplicación **Strands + MCP + LiteLLM** con FastAPI. El backend implementa un flujo agentico que:

1. Conecta a un **servidor MCP remoto** (p. ej., \`mcpokedex.com/mcp\`) vía MCPClient para descubrir herramientas
2. Usa **LiteLLM Router** para LLM calls con fallback multi-proveedor (Groq → Gemini → OpenAI)
3. Delega el **loop agentico a Strands**: LLM decide → herramienta ejecutada → resultado al LLM → repetir

\`\`\`python
# backend/agent.py
from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient

# 1. Crear el modelo LiteLLM (con Router + fallback)
model = LiteLLMModel(model_id="agent-model")

# 2. Conectar a servidor MCP remoto
mcp_client = MCPClient(lambda: streamablehttp_client("http://mcpokedex.com/mcp"))

# 3. Dentro de un contexto MCP, listar herramientas
with mcp_client:
    # list_tools_sync() retorna TODAS las herramientas del servidor MCP
    tools = mcp_client.list_tools_sync()
    
    # 4. Crear el agente con TODOS los tools
    agent = Agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )
    
    # 5. Strands maneja el loop internamente
    async for event in agent.stream_async(query):
        # Eventos: {"data": "texto"}, {"type": "tool_use", ...}, etc.
        # Strands automáticamente llama herramientas del servidor MCP
\`\`\`

**Arquitectura simplificada:**
- Solo herramientas **remotas** del servidor MCP (mcpokedex.com/mcp)
- Ejecución vía JSON-RPC over HTTP (~50-100ms de latencia típica)
- Descubrimiento dinámico: agregar nuevas herramientas en el servidor sin actualizar el backend

**Monkey-patching de LiteLLM:**
El LiteLLMModel de Strands llama \`litellm.completion()\` directamente. Como Strands no soporta nativo Router, el backend reemplaza las funciones de litellm globalmente ANTES de construir el modelo:

\`\`\`python
# Guardar originals antes de cualquier importación
_orig_completion = litellm.completion
_orig_acompletion = litellm.acompletion

# Reemplazar con Router
litellm.completion = router.completion
litellm.acompletion = router.acompletion

# Ahora crear el modelo → todas sus llamadas van por el Router
model = LiteLLMModel(model_id="agent-model")
\`\`\`

Un ContextVar (\`_in_router_call\`) previene bucles infinitos: cuando el Router internamente llama \`litellm.completion\`, se detecta y se usa la función original.
`,

  tool_pokemon: `
### ¿Cómo funciona esta herramienta?

\`get_pokedex_entry\` es una herramienta **remota del servidor MCP** (mcpokedex.com/mcp).

El agente (**Strands Agent**) descubre esta herramienta a través de MCPClient y la ejecuta vía JSON-RPC:

1. Backend se conecta al servidor MCP remoto
2. MCPClient lista todas las herramientas disponibles (incluyendo \`get_pokedex_entry\`)
3. El LLM elige \`get_pokedex_entry\` según el contexto
4. La herramienta se ejecuta en el servidor remoto vía JSON-RPC
5. El resultado retorna al agente para continuar la conversación

\`\`\`python
# En el servidor MCP remoto (mcpokedex.com/mcp)
from mcp.tool import tool

@tool()
def get_pokedex_entry(pokemon: str) -> dict:
    """Fetch a complete Pokédex entry for a Pokémon from PokeAPI.

    Retrieves types, base stats, abilities, height, weight, flavor text
    descriptions from the games (en Spanish when available, otherwise English),
    generation, habitat, legendary/mythical status, and capture rate.

    Args:
        pokemon: The Pokémon name or National Pokédex number as string

    Returns:
        Dict with complete pokedex entry or error status
    """
    # Conecta a PokeAPI
    _POKEAPI_BASE = "https://pokeapi.co/api/v2"
    name_or_id = urllib.parse.quote(pokemon.strip().lower(), safe="")
    
    with httpx.Client(timeout=10.0) as client:
        r_poke = client.get(f"{_POKEAPI_BASE}/pokemon/{name_or_id}")
        species_url = r_poke.json().get("species", {}).get("url")
        species = client.get(species_url).json()
    
    # Extract types, stats, abilities, flavor text, generation, habitat, etc.
    return {"status": "success", "data": {...}}
\`\`\`

**Ventaja de herramientas remotas**: Descubrimiento dinámico y ejecución centralizada en el servidor MCP sin necesidad de actualizar el backend.
`,

  skill: `
### ¿Qué son las Skills?

Las **Skills** son módulos de instrucciones especializadas que el agente Dexter carga dinámicamente durante una conversación. Cada skill contiene directrices que ajustan su estilo de respuesta y conocimiento para una tarea concreta.

\`\`\`
backend/skills/dexter-pokedex-narrator/SKILL.md
  → instrucciones de narración pokédex
  → tono, formato, ejemplos de respuesta
\`\`\`

Cuando el agente invoca la herramienta \`skills\`, las instrucciones del skill se inyectan como contexto adicional en su prompt, cambiando su comportamiento **sin reiniciar la sesión**.
`,

  tool_generic: `
### ¿Cómo funciona esta herramienta (Remota)?

Todas las herramientas se exponen a través del **Protocolo de Contexto del Modelo (MCP)** desde el servidor remoto (\`mcpokedex.com/mcp\`) e integradas con **Strands**.

**Flujo de descubrimiento y ejecución:**
1. Backend crea MCPClient (cliente HTTP + JSON-RPC del protocolo MCP)
2. MCPClient se conecta al servidor MCP remoto durante cada request
3. \`mcp_client.list_tools_sync()\` descubre todas las herramientas disponibles
4. Strands Agent recibe el catálogo completo de herramientas MCP
5. El LLM decide qué herramienta usar según el contexto del usuario
6. Cuando el LLM elige una herramienta:
   - Strands invoca MCPClient para ejecutar la herramienta
   - MCPClient realiza el RPC en el servidor remoto (JSON-RPC over HTTP)
   - El servidor ejecuta la herramienta (típicamente consultando PokeAPI o procesando datos)
   - El resultado retorna al agente para continuar el diálogo

\`\`\`python
# backend/main.py - Arquitectura MCP
from strands.tools.mcp import MCPClient

# Crear cliente MCP
mcp_client = MCPClient(
    lambda: streamablehttp_client("http://mcpokedex.com/mcp")
)

# Dentro del contexto MCP
with mcp_client:
    # Discover todas las herramientas del servidor MCP
    tools = mcp_client.list_tools_sync()
    
    # Crear agente con Strands
    agent = Agent(
        model=model,
        tools=tools,  # Solo herramientas remotas del MCP
        system_prompt=SYSTEM_PROMPT,
    )
    
    # Strands maneja el loop agentico
    async for event in agent.stream_async(query):
        # {"type": "tool_use", "name": "...", "input": {...}}
        # Strands ejecuta la herramienta remotamente vía MCPClient
\`\`\`

**Características de MCP:**
- **Descubrimiento dinámico**: Agregar nuevas herramientas en el servidor remoto sin actualizar el backend
- **Ejecución remota**: JSON-RPC over HTTP, ~50-100ms de latencia típica
- **Compatible con cualquier servidor MCP**: Arquitectura agnóstica y extensible

**Ventaja**: Mayor flexibilidad y escalabilidad al centralizar la lógica de herramientas en el servidor MCP.
`,
}

function getToolDoc(tool: string): string {
  const name = tool.toLowerCase()
  if (name.includes('pokedex') || name.includes('pokemon')) return DOCS.tool_pokemon
  return DOCS.tool_generic
}

// ── EduPanel ──────────────────────────────────────────────────────

function EduPanel({ markdown }: { markdown: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={styles.eduContainer}>
      <button className={styles.eduToggleBtn} onClick={() => setOpen((v) => !v)}>
        {open ? '[cerrar]' : '💡 ¿Cómo funciona esto?'}
      </button>
      {open && (
        <div className={styles.eduContent}>
          <ReactMarkdown>{markdown}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

interface Props {
  event: TraceEvent
  /** For tool_result: reference to the matching tool_call card index */
  toolCallIndex?: number
}

// ── LLM Call ──────────────────────────────────────────────────────

function LlmCallCard({ e }: { e: LlmCallEvent }) {
  const [open, setOpen] = useState(false)
  const messages = e.messages ?? []
  const userMsg  = [...messages].reverse().find((m) => m.role === 'user')
  const preview  = (() => {
    if (!userMsg) return ''
    const raw = typeof userMsg.content === 'string'
      ? userMsg.content
      : JSON.stringify(userMsg.content)
    return raw.slice(0, 100)
  })()

  return (
    <div className={`${styles.card} ${styles.llm}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles.llm}`}>PROMPT</span>
        <span className={styles.name}>LLM Call #{e.call_index}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
      </div>
      {preview && (
        <div className={styles.meta} style={{ fontStyle: 'italic' }}>
          &ldquo;{preview}{preview.length >= 100 ? '…' : ''}&rdquo;
        </div>
      )}
      <EduPanel markdown={DOCS.agent} />
      <div className={styles.actionContainer}>
        <button
          className={styles.actionBtn}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '[cerrar]' : '🔍 ¿cómo funciona por dentro?'}
        </button>
        {open && (
          <div className={styles.expandable}>
            <JsonViewer data={messages} maxHeight="400px" />
          </div>
        )}
      </div>
    </div>
  )
}

// ── Model Attempt ─────────────────────────────────────────────────

function ModelAttemptCard({ e }: { e: ModelAttemptEvent }) {
  const typeKey = e.status === 'success' ? 'modelOk' : 'modelFail'
  const icon    = e.status === 'success' ? '✓' : '✗'

  return (
    <div className={`${styles.card} ${styles[typeKey]}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles[typeKey]}`}>LLM</span>
        <span className={styles.name}>{e.model ?? 'unknown'}</span>
        <span className={styles.timing}>{icon} +{e.timestamp_ms ?? 0}ms</span>
      </div>
      {e.error && (
        <div className={styles.meta} style={{ color: '#b84a4a' }}>{e.error}</div>
      )}
    </div>
  )
}

// ── Skill Call ────────────────────────────────────────────────────

function SkillCallCard({ e }: { e: ToolCallEvent }) {
  const skillName = (e.args as Record<string, unknown>)?.skill_name as string ?? e.tool

  return (
    <div className={`${styles.card} ${styles.skill}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles.skill}`}>SKILL</span>
        <span className={styles.name}>{skillName}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
      </div>
      <div className={styles.meta}>
        Cargando instrucciones de skill para el agente Dexter...
      </div>
      <EduPanel markdown={DOCS.skill} />
    </div>
  )
}

// ── Tool Call ─────────────────────────────────────────────────────

function ToolCallCard({ e }: { e: ToolCallEvent }) {
  const typeKey = guessToolType(e.tool)

  if (typeKey === 'skill') return <SkillCallCard e={e} />

  return (
    <div className={`${styles.card} ${styles[typeKey]}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles[typeKey]}`}>{typeKey.toUpperCase()}</span>
        <span className={styles.name}>{e.tool}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
      </div>
      <JsonViewer data={e.args ?? {}} maxHeight="120px" />
      <EduPanel markdown={getToolDoc(e.tool)} />
    </div>
  )
}

// ── Tool Result ───────────────────────────────────────────────────

function ToolResultCard({ e }: { e: ToolResultEvent }) {
  const [open, setOpen] = useState(false)
  const full    = String(e.result ?? '')
  const parsed  = tryParseJson(full)

  return (
    <div className={`${styles.card} ${styles.result}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles.result}`}>RESULT</span>
        <span className={styles.name}>Tool Result #{e.index}</span>
      </div>
      {parsed !== null ? (
        <JsonViewer data={parsed} maxHeight="160px" />
      ) : (
        <>
          <div className={styles.meta} style={{ marginTop: 3, borderTop: '1px dashed var(--gbc-border)', paddingTop: 3 }}>
            <span>Resultado:</span> {full.slice(0, 300)}{full.length > 300 ? '…' : ''}
          </div>
          {full.length > 300 && (
            <div className={styles.actionContainer}>
              <button className={styles.actionBtn} onClick={() => setOpen((v) => !v)}>
                {open ? '[cerrar]' : '📄 ver todo'}
              </button>
              {open && (
                <div className={styles.expandable}>
                  <pre className={styles.resultFull}>{full}</pre>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── System Log ────────────────────────────────────────────────────

function SystemLogCard({ e }: { e: SystemLogEvent }) {
  return (
    <div className={`${styles.systemLog} ${styles[e.level]}`}>
      {e.message}
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────────

export default function TraceCard({ event }: Props) {
  switch (event.type) {
    case 'llm_call':      return <LlmCallCard      e={event as LlmCallEvent}      />
    case 'model_attempt': return <ModelAttemptCard e={event as ModelAttemptEvent} />
    case 'tool_call':     return <ToolCallCard      e={event as ToolCallEvent}     />
    case 'tool_result':   return <ToolResultCard   e={event as ToolResultEvent}   />
    case 'system_log':    return <SystemLogCard    e={event as SystemLogEvent}    />
    default:              return null
  }
}
