import type { AgentInitEvent, ToolCallEvent, ToolResultEvent, TraceEvent } from '../../types'

// Folds the raw SSE trace into the few steps a person cares about:
// a model call plus its failovers is one "think" step, and a batch of
// tool calls between two model calls is one "tools" step with its results.
//
// Events tagged with `agent` are the steps of a sub-agent (Bill): they fold the
// same way, but into the tool run that called that agent, so the timeline shows
// them nested under "Delegó en bill" instead of mixed with Dexter's own steps.

export interface ModelAttempt {
  model: string
  status: 'pending' | 'success' | 'failed'
  error?: string
  messages: Array<{ role: string; content: string }>
}

export interface ToolRun {
  call: ToolCallEvent
  result?: ToolResultEvent
  /** What the agent behind this call did, when the tool is an agent (`bill`). */
  steps?: Step[]
}

export type Step =
  | { kind: 'init'; at: number; event: AgentInitEvent }
  | { kind: 'think'; at: number; attempts: ModelAttempt[] }
  | { kind: 'tools'; at: number; runs: ToolRun[] }
  | { kind: 'skill'; at: number; run: ToolRun }

export const isSkillTool = (tool: string) => tool === 'skills' || tool === 'skill'

/** Agents Dexter calls as a tool (backend/agent.py::AGENT_TOOLS). */
const AGENT_TOOLS = new Set(['bill'])
export const isAgentTool = (tool: string) => AGENT_TOOLS.has(tool)

/** Fold one event into a list of steps (Dexter's, or a sub-agent's). */
function fold(steps: Step[], e: TraceEvent, runsById: Map<string, ToolRun>): ToolRun | undefined {
  const prev = steps[steps.length - 1]
  switch (e.type) {
    case 'agent_init':
      steps.push({ kind: 'init', at: e.timestamp_ms, event: e })
      return

    case 'llm_call': {
      const attempt: ModelAttempt = { model: e.model, status: 'pending', messages: e.messages ?? [] }
      // A failover or retry re-calls the model for the same step.
      if (prev?.kind === 'think' && prev.attempts[prev.attempts.length - 1].status === 'failed') {
        prev.attempts.push(attempt)
      } else {
        steps.push({ kind: 'think', at: e.timestamp_ms, attempts: [attempt] })
      }
      return
    }

    case 'model_attempt': {
      if (prev?.kind !== 'think') return
      const attempt = prev.attempts[prev.attempts.length - 1]
      attempt.status = e.status
      attempt.error = e.error
      return
    }

    case 'tool_call': {
      const run: ToolRun = { call: e }
      runsById.set(e.tool_id, run)
      if (isSkillTool(e.tool)) {
        steps.push({ kind: 'skill', at: e.timestamp_ms, run })
      } else if (prev?.kind === 'tools') {
        prev.runs.push(run)
      } else {
        steps.push({ kind: 'tools', at: e.timestamp_ms, runs: [run] })
      }
      return run
    }

    case 'tool_result': {
      const run = runsById.get(e.tool_id)
      if (run) run.result = e
      return
    }
  }
}

export function buildSteps(traces: TraceEvent[]): Step[] {
  const steps: Step[] = []
  const runsById = new Map<string, ToolRun>()
  // The latest call to each agent-tool: where that agent's own events nest.
  const agentRuns = new Map<string, ToolRun>()

  for (const e of traces) {
    const agent = 'agent' in e ? e.agent : undefined
    const parent = agent ? agentRuns.get(agent) : undefined
    if (parent) {
      fold((parent.steps ??= []), e, runsById)
      continue
    }
    // An agent event with no call to nest under (never expected) stays visible at the top level.
    const run = fold(steps, e, runsById)
    if (run && !agent && isAgentTool(run.call.tool)) agentRuns.set(run.call.tool, run)
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
