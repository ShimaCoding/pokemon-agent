import type { TraceDetail, TraceSummary } from './types'

// Same key and storage slot as the admin panel (backend/static/admin.html): one login for both.
const KEY_SLOT = 'adminApiKey'

export class Unauthorized extends Error {}

export function hasKey(): boolean {
  return Boolean(sessionStorage.getItem(KEY_SLOT))
}

export function saveKey(key: string): void {
  sessionStorage.setItem(KEY_SLOT, key)
}

async function get<T>(path: string): Promise<T> {
  const key = sessionStorage.getItem(KEY_SLOT)
  const res = await fetch(path, { headers: key ? { 'X-API-Key': key } : {} })
  if (res.status === 401) throw new Unauthorized()
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
    throw new Error(typeof body?.detail === 'string' ? body.detail : `El servidor respondió ${res.status}.`)
  }
  return (await res.json()) as T
}

export async function listTraces(): Promise<TraceSummary[]> {
  return (await get<{ traces: TraceSummary[] }>('/admin/api/traces')).traces
}

export function getTrace(traceId: string): Promise<TraceDetail> {
  return get<TraceDetail>(`/admin/api/traces/${traceId}`)
}
