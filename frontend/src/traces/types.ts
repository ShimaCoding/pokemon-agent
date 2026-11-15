// What /admin/api/traces returns (backend/traces.py, read from Phoenix).

export type SpanKind = 'agent' | 'llm' | 'tool'

export interface ChatMessage {
  role: string
  content: string
}

export interface TraceSpan {
  id: string
  parentId: string | null
  kind: SpanKind
  /** Agent name, tool name, or the model id for llm spans. */
  name: string
  error: boolean
  /** Message of the exception, when there is one. */
  errorMessage?: string
  /** Milliseconds since the trace started. */
  startMs: number
  durationMs: number
  /** llm spans: prompt and completion tokens of that call. Agents: the sum of everything under them. */
  tokens?: { prompt: number; completion: number; cached?: number }
  /** llm: the messages sent. tool: its arguments. agent: the question it got. */
  input?: string | ChatMessage[] | Record<string, unknown> | unknown[]
  output?: string
  /** Tools the model asked for in this call. */
  toolCalls?: string[]
  metadata?: Record<string, unknown>
}

export interface TraceSummary {
  traceId: string
  startedAt: string
  query: string
  durationMs: number
  tokens: number
  error: boolean
  spanCount: number
  subAgents: number
}

export interface TraceDetail extends TraceSummary {
  spans: TraceSpan[]
  /** The trace in Phoenix's UI; null when the server has no PHOENIX_PUBLIC_URL. */
  phoenixUrl: string | null
}
