// ── Pokémon Data ────────────────────────────────────────────────

export interface PokemonStat {
  hp: number
  atk: number
  def: number
  spatk: number
  spdef: number
  spd: number
}

export interface Weakness {
  type: string
  multiplier: number
}

export interface EvoStep {
  name: string
  sprite: string
}

export interface MoveEntry {
  name: string
  type?: string
}

export interface PokemonData {
  name: string
  number: number
  types: string[]
  height: string
  weight: string
  sprite: string
  stats: PokemonStat
  moves: MoveEntry[]
  dex_entry: string
  evolution_chain: EvoStep[]
  weaknesses: Weakness[]
}

// ── SSE Event Union ─────────────────────────────────────────────

export interface LlmCallEvent {
  type: 'llm_call'
  call_index: number
  timestamp_ms: number
  messages: Array<{ role: string; content: string | unknown }>
}

export interface ModelAttemptEvent {
  type: 'model_attempt'
  model: string
  status: 'success' | 'fail'
  timestamp_ms: number
  error?: string
}

export interface ToolCallEvent {
  type: 'tool_call'
  tool: string
  args: Record<string, unknown>
  index: number
  timestamp_ms: number
}

export interface ToolResultEvent {
  type: 'tool_result'
  result: string
  index: number
}

export interface TextEvent {
  type: 'text'
  delta: string
}

export interface DoneEvent {
  type: 'done'
  elapsed_ms: number
  models_tried: string[]
  total_tool_calls?: number
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
}

export interface ErrorEvent {
  type: 'error'
  message?: string
}

export interface SystemLogEvent {
  type: 'system_log'
  message: string
  level: 'info' | 'warn' | 'success'
}

export type TraceEvent =
  | LlmCallEvent
  | ModelAttemptEvent
  | ToolCallEvent
  | ToolResultEvent
  | TextEvent
  | DoneEvent
  | ErrorEvent
  | SystemLogEvent

// ── Providers & Prompts ─────────────────────────────────────────

export interface Provider {
  name: string
  label: string
  available: boolean
}

export interface PromptArg {
  name: string
  description?: string
  required?: boolean
}

export interface McpPrompt {
  name: string
  description?: string
  arguments?: PromptArg[]
}

export interface SlashPrompt {
  group: string
  name: string
  desc: string
  template: string
  isMcp?: boolean
}

export interface McpTool {
  name: string
  description: string
  input_schema?: Record<string, unknown>
}

export interface McpResource {
  uri: string
  name: string
  description: string
  mime_type: string
}

// ── UI ──────────────────────────────────────────────────────────

export type TabKey = 'info' | 'stats' | 'moves' | 'evos' | 'dexter' | 'consola'
