import type { AgentInitEvent, ToolCallEvent, ToolResultEvent, TraceEvent } from '../../types'

// Folds the raw SSE trace into the few steps a person cares about:
// a model call plus its failovers is one "think" step, and a batch of
// tool calls between two model calls is one "tools" step with its results.

export interface ModelAttempt {
  model: string
  status: 'pending' | 'success' | 'failed'
  error?: string
  messages: Array<{ role: string; content: string }>
}

export interface ToolRun {
  call: ToolCallEvent
  result?: ToolResultEvent
}

export type Step =
  | { kind: 'init'; at: number; event: AgentInitEvent }
  | { kind: 'think'; at: number; attempts: ModelAttempt[] }
  | { kind: 'tools'; at: number; runs: ToolRun[] }
  | { kind: 'skill'; at: number; run: ToolRun }

export const isSkillTool = (tool: string) => tool === 'skills' || tool === 'skill'

export function buildSteps(traces: TraceEvent[]): Step[] {
  const steps: Step[] = []
  const runsById = new Map<string, ToolRun>()
  const last = () => steps[steps.length - 1]

  for (const e of traces) {
    switch (e.type) {
      case 'agent_init':
        steps.push({ kind: 'init', at: e.timestamp_ms, event: e })
        break

      case 'llm_call': {
        const attempt: ModelAttempt = { model: e.model, status: 'pending', messages: e.messages ?? [] }
        const prev = last()
        // A failover or retry re-calls the model for the same step.
        if (prev?.kind === 'think' && prev.attempts[prev.attempts.length - 1].status === 'failed') {
          prev.attempts.push(attempt)
        } else {
          steps.push({ kind: 'think', at: e.timestamp_ms, attempts: [attempt] })
        }
        break
      }

      case 'model_attempt': {
        const prev = last()
        if (prev?.kind !== 'think') break
        const attempt = prev.attempts[prev.attempts.length - 1]
        attempt.status = e.status
        attempt.error = e.error
        break
      }

      case 'tool_call': {
        const run: ToolRun = { call: e }
        runsById.set(e.tool_id, run)
        const prev = last()
        if (isSkillTool(e.tool)) {
          steps.push({ kind: 'skill', at: e.timestamp_ms, run })
        } else if (prev?.kind === 'tools') {
          prev.runs.push(run)
        } else {
          steps.push({ kind: 'tools', at: e.timestamp_ms, runs: [run] })
        }
        break
      }

      case 'tool_result': {
        const run = runsById.get(e.tool_id)
        if (run) run.result = e
        break
      }
    }
  }
  return steps
}

/** Short human summary of a tool's arguments, e.g. `snorlax` or `snorlax, lapras`. */
export function argSummary(args: Record<string, unknown> | undefined): string {
  const values = Object.values(args ?? {})
    .flat()
    .filter((v): v is string | number => typeof v === 'string' || typeof v === 'number')
  return values.map(String).join(', ')
}

export function shortModel(model: string): string {
  return model.split('/').pop() ?? model
}

export function formatMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}
