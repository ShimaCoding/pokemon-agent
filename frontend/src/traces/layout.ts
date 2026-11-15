import type { TraceSpan } from './types'

export interface FlowNode {
  span: TraceSpan
  /** Steps in the order they ran. A row with several nodes ran at the same time. */
  rows: FlowNode[][]
}

/** Span list → the root agent with its steps grouped into rows. */
export function buildFlow(spans: TraceSpan[]): FlowNode | null {
  const byParent = new Map<string | null, TraceSpan[]>()
  const ids = new Set(spans.map((s) => s.id))
  for (const span of spans) {
    // A span whose parent never arrived hangs from the top.
    const parent = span.parentId && ids.has(span.parentId) ? span.parentId : null
    byParent.set(parent, [...(byParent.get(parent) ?? []), span])
  }

  const build = (span: TraceSpan): FlowNode => {
    const children = (byParent.get(span.id) ?? []).sort((a, b) => a.startMs - b.startMs)
    const rows: FlowNode[][] = []
    let rowEnd = -Infinity
    for (const child of children) {
      // It started before the previous step finished: they ran in parallel.
      if (child.startMs < rowEnd) rows[rows.length - 1].push(build(child))
      else rows.push([build(child)])
      rowEnd = Math.max(rowEnd, child.startMs + child.durationMs)
    }
    return { span, rows }
  }

  const [root] = (byParent.get(null) ?? []).sort((a, b) => a.startMs - b.startMs)
  return root ? build(root) : null
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  // Decimal comma, like the thousands dot in formatTokens: "4,18 s" next to "15.691 tokens".
  return `${(ms / 1000).toLocaleString('es-CL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} s`
}

export function formatTokens(n: number): string {
  return n.toLocaleString('es-CL')
}

export function totalTokens(span: TraceSpan): number | null {
  return span.tokens ? span.tokens.prompt + span.tokens.completion : null
}
