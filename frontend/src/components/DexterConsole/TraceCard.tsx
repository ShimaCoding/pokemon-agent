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

El agente **Dexter** está construido con el SDK de **Strands Agents**. El backend crea un \`LiteLLMModel\` (con fallback automático entre proveedores via Router) y un \`MCPClient\` que se conecta al servidor MCP remoto para obtener las herramientas disponibles.

\`\`\`python
# backend/agent.py
from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient

SYSTEM_PROMPT = """
Eres Dexter, una Pokédex de alta tecnología programada
por el Profesor Oak. Tu objetivo es proporcionar información
precisa y científica sobre los Pokémon...
"""

def build_agent(provider_name=None):
    model = LiteLLMModel(model_id="agent-model")
    mcp_client = MCPClient(lambda: _http_transport(MCP_SERVER_URL))

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
        )
        return agent
\`\`\`

Antes de construir el modelo, el backend **parchea globalmente** \`litellm.completion\` con el Router para que todos los llamados pasen por el mecanismo de fallback (Groq → Gemini → OpenAI) sin modificar el SDK de Strands.
`,

  tool_pokemon: `
### ¿Cómo funciona esta herramienta?

\`get_pokedex_entry\` es una herramienta **local de Strands**, decorada con \`@tool\`. Cuando el agente la necesita, la ejecuta directamente en el backend (sin pasar por MCP), llamando a la **PokeAPI** en dos endpoints: \`/pokemon/{id}\` para datos base y \`/pokemon-species/{id}\` para flavor text y metadatos de especie.

\`\`\`python
# backend/tools.py
import json
import httpx
from strands import tool

_POKEAPI_BASE = "https://pokeapi.co/api/v2"

@tool
def get_pokedex_entry(pokemon: str) -> dict:
    """Fetch a complete Pokédex entry for a Pokémon from PokeAPI.

    Retrieves types, base stats, abilities, height, weight, flavor text
    descriptions from the games (in Spanish when available, otherwise
    English), generation, habitat, legendary/mythical status, and capture
    rate. Use this tool whenever the user asks about a specific Pokémon.

    Args:
        pokemon: The Pokémon name (lowercase, e.g. "pikachu") or National
                 Pokédex number as a string (e.g. "25").

    Returns:
        A dict with full Pokédex entry data, or an error status on failure.
    """
    name_or_id = pokemon.strip().lower()
    with httpx.Client(timeout=10.0) as client:
        r_poke = client.get(f"{_POKEAPI_BASE}/pokemon/{name_or_id}")
        if r_poke.status_code == 404:
            return {"status": "error", "content": [{"text": f"No se encontró '{pokemon}'."}]}
        r_poke.raise_for_status()
        poke = r_poke.json()

        species_url = poke.get("species", {}).get("url", f"{_POKEAPI_BASE}/pokemon-species/{name_or_id}")
        species = client.get(species_url).json()

    types = [t["type"]["name"] for t in sorted(poke["types"], key=lambda x: x["slot"])]
    base_stats = {s["stat"]["name"]: s["base_stat"] for s in poke["stats"]}
    abilities = [{"name": a["ability"]["name"], "is_hidden": a["is_hidden"]}
                 for a in sorted(poke["abilities"], key=lambda x: x["slot"])]

    # Flavor texts: prefer Spanish, fallback to English; deduplicate; cap at 3
    def _collect_texts(lang):
        seen, result = set(), []
        for entry in species.get("flavor_text_entries", []):
            if entry["language"]["name"] == lang:
                text = entry["flavor_text"].replace("\\f", " ").replace("\\n", " ").strip()
                if text and text not in seen:
                    seen.add(text); result.append(text)
                    if len(result) == 3: break
        return result

    flavor_texts = _collect_texts("es") or _collect_texts("en")
    generation = species.get("generation", {}).get("name", "").replace("generation-", "").upper()
    habitat_obj = species.get("habitat")

    return {
        "status": "success",
        "content": [{"text": json.dumps({
            "id": poke["id"], "name": poke["name"],
            "height_dm": poke["height"], "weight_hg": poke["weight"],
            "types": types, "base_stats": base_stats, "abilities": abilities,
            "flavor_text": flavor_texts, "generation": generation,
            "habitat": habitat_obj["name"] if habitat_obj else "unknown",
            "is_legendary": species.get("is_legendary", False),
            "is_mythical": species.get("is_mythical", False),
            "capture_rate": species.get("capture_rate"),
        }, ensure_ascii=False)}],
    }
\`\`\`

El decorador \`@tool\` genera automáticamente el **JSON Schema** del argumento a partir del type hint y el docstring, para que el LLM sepa exactamente cómo invocar la función.
`,

  tool_generic: `
### ¿Cómo funciona esta herramienta MCP?

Las herramientas remotas se exponen a través del **servidor MCP** (Model Context Protocol). El servidor las registra con el decorador \`@mcp.tool()\` y las publica vía HTTP. El agente las descubre dinámicamente en cada request.

\`\`\`python
# En el servidor MCP (mcpokedex.com/mcp)
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Pokemon MCP Server")

@mcp.tool()
def search_pokemon(query: str) -> dict:
    """Search for a Pokémon by name or type.

    Args:
        query: Name or type to search for.
    Returns:
        List of matching Pokémon with basic info.
    """
    # ... lógica de búsqueda
    return results
\`\`\`

El cliente MCP en el backend usa \`MCPClient.list_tools_sync()\` para obtener el **catálogo completo** de herramientas al inicio de cada petición, y las pasa al agente Strands como tools disponibles.
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
        <button
          className={styles.toggleBtn}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '[ocultar]' : '[ver msgs]'}
        </button>
      </div>
      {preview && (
        <div className={styles.meta} style={{ fontStyle: 'italic' }}>
          &ldquo;{preview}{preview.length >= 100 ? '…' : ''}&rdquo;
        </div>
      )}
      <EduPanel markdown={DOCS.agent} />
      {open && (
        <div className={styles.expandable}>
          {messages.map((m, i) => {
            const parsed = typeof m.content === 'string'
              ? tryParseJson(m.content)
              : m.content
            return (
              <div key={i}>
                <div className={styles.msgRole}>[{m.role}]</div>
                {parsed !== null && typeof parsed === 'object' ? (
                  <JsonViewer data={parsed} maxHeight="200px" />
                ) : (
                  <pre className={styles.msgContent}>
                    {typeof m.content === 'string' ? m.content : JSON.stringify(m.content, null, 2)}
                  </pre>
                )}
              </div>
            )
          })}
        </div>
      )}
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

// ── Tool Call ─────────────────────────────────────────────────────

function ToolCallCard({ e }: { e: ToolCallEvent }) {
  const typeKey = guessToolType(e.tool)

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
        <div className={styles.meta} style={{ marginTop: 3, borderTop: '1px dashed var(--gbc-border)', paddingTop: 3 }}>
          <span>Resultado:</span> {full.slice(0, 300)}{full.length > 300 ? '…' : ''}
          {full.length > 300 && (
            <>
              {' '}
              <button className={styles.toggleBtn} onClick={() => setOpen((v) => !v)}>
                {open ? '[ocultar]' : '[ver todo]'}
              </button>
              {open && (
                <div className={styles.expandable}>
                  <pre className={styles.resultFull}>{full}</pre>
                </div>
              )}
            </>
          )}
        </div>
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
