// ── SSE events (see backend/main.py::_sse_generator) ─────────────

export interface StartEvent {
  type: 'start'
  provider: string
  conversation_id: string
}

export interface AgentInitEvent {
  type: 'agent_init'
  mcp_tools_count: number
  tool_names: string[]
  skill_loaded: string | null
  system_prompt_preview: string | null
  history_messages: number
  // 1-based turn of the conversation (#18); missing in traces saved before it.
  turn?: number
  timestamp_ms: number
}

export interface LlmCallEvent {
  type: 'llm_call'
  call_index: number
  model: string
  timestamp_ms: number
  messages: Array<{ role: string; content: string }>
  /** routing: opens the turn (picks a tool or answers directly); final: after tool results. */
  call_role?: 'routing' | 'final'
  reasoning_effort?: string
}

export interface ModelAttemptEvent {
  type: 'model_attempt'
  model: string
  status: 'success' | 'failed'
  timestamp_ms: number
  error?: string
}

export interface ToolCallEvent {
  type: 'tool_call'
  index: number
  tool_id: string
  tool: string
  args: Record<string, unknown>
  timestamp_ms: number
}

export interface ToolResultEvent {
  type: 'tool_result'
  index: number
  tool_id: string
  tool: string
  result: string
  timestamp_ms: number
  /** Served from the agent's tool cache, without calling the MCP server. */
  cached?: boolean
  duration_ms?: number | null
}

/** MCP `CallToolResult` wire shape, as handed to widgets. */
export interface CallToolResult {
  content: Array<{ type: 'text'; text: string }>
  structuredContent?: Record<string, unknown>
  isError?: boolean
}

export interface UiResourceEvent {
  type: 'ui_resource'
  index: number
  tool_id: string
  tool: string
  resource_uri: string
  tool_input: Record<string, unknown>
  tool_result: CallToolResult
  timestamp_ms: number
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
  cached_tokens?: number
  llm_calls?: number
  ttft_ms?: number | null
}

export interface ErrorEvent {
  type: 'error'
  message?: string
}

export type SseEvent =
  | StartEvent
  | AgentInitEvent
  | LlmCallEvent
  | ModelAttemptEvent
  | ToolCallEvent
  | ToolResultEvent
  | UiResourceEvent
  | TextEvent
  | DoneEvent
  | ErrorEvent

/** Events kept for the "ver cómo lo pensó" panel. */
export type TraceEvent = AgentInitEvent | LlmCallEvent | ModelAttemptEvent | ToolCallEvent | ToolResultEvent

// ── Chat ─────────────────────────────────────────────────────────

/** An assistant reply is an ordered list of text and widget parts, as they streamed in. */
export type MessagePart =
  | { kind: 'text'; text: string }
  | { kind: 'widget'; event: UiResourceEvent }

export interface UserMessage {
  id: string
  role: 'user'
  text: string
}

export interface AssistantMessage {
  id: string
  role: 'assistant'
  parts: MessagePart[]
  traces: TraceEvent[]
  status: 'streaming' | 'done' | 'error'
  done?: DoneEvent
  error?: string
}

export type ChatMessage = UserMessage | AssistantMessage

// ── MCP Apps ─────────────────────────────────────────────────────

export interface UiResourceCsp {
  connectDomains?: string[]
  resourceDomains?: string[]
  frameDomains?: string[]
  baseUriDomains?: string[]
}

/** Response of GET /api/ui-resource. */
export interface UiResource {
  uri: string
  mimeType: string
  html: string
  csp: UiResourceCsp
  permissions: Record<string, unknown>
  prefersBorder?: boolean | null
}

// ── Wiki content (lessons shown in the trace panel) ──────────────

export interface WikiLesson {
  id: string
  title: string
  level: string
  tags: string[]
  related_events: string[]
  related_nodes: string[]
  body_md: string
}

export interface EventCatalogEntry {
  description_md: string
  related_lesson_id: string
  related_node_id: string
}

export type EventsCatalog = Record<string, EventCatalogEntry>

// ── MCP capabilities ─────────────────────────────────────────────

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
  ui_resource_uri?: string | null
}

export interface McpResource {
  uri: string
  name: string
  description: string
  mime_type: string
}
