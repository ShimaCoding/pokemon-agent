import { useSettings } from '../store/useStore'
import type { CallToolResult, FeedbackValue, UiResource } from '../types'

export function authHeaders(): Record<string, string> {
  const apiKey = useSettings.getState().apiKey
  return apiKey ? { 'X-API-Key': apiKey } : {}
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    /* not JSON */
  }
  return `HTTP ${res.status}`
}

// Widget HTML is static per deploy: fetch each ui:// resource once per page load.
const uiResourceCache = new Map<string, Promise<UiResource>>()

export function fetchUiResource(uri: string): Promise<UiResource> {
  let pending = uiResourceCache.get(uri)
  if (!pending) {
    pending = (async () => {
      const res = await fetch(`/api/ui-resource?uri=${encodeURIComponent(uri)}`, { headers: authHeaders() })
      if (!res.ok) throw new Error(await errorDetail(res))
      return (await res.json()) as UiResource
    })()
    // Don't cache failures: a later widget may retry.
    pending.catch(() => uiResourceCache.delete(uri))
    uiResourceCache.set(uri, pending)
  }
  return pending
}

/** Forward a widget's `callServerTool` through the backend (the browser has no MCP client). */
export async function callServerTool(name: string, args: Record<string, unknown>): Promise<CallToolResult> {
  const res = await fetch('/api/mcp/call-tool', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ name, arguments: args }),
  })
  if (!res.ok) {
    return { content: [{ type: 'text', text: await errorDetail(res) }], isError: true }
  }
  return (await res.json()) as CallToolResult
}

/** 👍/👎 on a reply, tied to its Datadog trace. 409 means it was already rated. */
export async function sendFeedback(traceId: string, spanId: string, value: FeedbackValue): Promise<'ok' | 'voted' | 'error'> {
  try {
    const res = await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trace_id: traceId, span_id: spanId, value }),
    })
    if (res.status === 204) return 'ok'
    if (res.status === 409) return 'voted'
    return 'error'
  } catch {
    return 'error'
  }
}
