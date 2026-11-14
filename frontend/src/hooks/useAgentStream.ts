import { authHeaders } from '../lib/api'
import { useChat } from '../store/useStore'
import type { DoneEvent, SseEvent } from '../types'

const MAX_QUERY_LENGTH = 500

// Module-level guard: blocks a second run even before React re-renders with inFlight=true.
let running = false
let rateLimitTimer: ReturnType<typeof setInterval> | null = null

function startRateLimitCountdown(seconds: number) {
  const chat = useChat.getState()
  chat.setRateLimitSeconds(seconds)
  if (rateLimitTimer) clearInterval(rateLimitTimer)
  rateLimitTimer = setInterval(() => {
    useChat.getState().setRateLimitSeconds((s) => {
      if (s <= 1 && rateLimitTimer) {
        clearInterval(rateLimitTimer)
        rateLimitTimer = null
      }
      return Math.max(0, s - 1)
    })
  }, 1000)
}

/** Parse an SSE body (`data: <json>\n\n` frames) into events. */
async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.trim()
      if (!line.startsWith('data:')) continue
      try {
        yield JSON.parse(line.slice(5).trim()) as SseEvent
      } catch {
        /* skip malformed frame */
      }
    }
  }
}

/**
 * Send a message in the current conversation and stream the reply into the
 * chat store. Also used by widgets (`app.sendMessage`).
 */
export async function sendChatMessage(rawQuery: string): Promise<void> {
  const query = rawQuery.trim()
  const chat = useChat.getState()
  if (!query || query.length > MAX_QUERY_LENGTH || running || chat.rateLimitSeconds > 0) return

  running = true
  chat.setInFlight(true)
  chat.addUserMessage(query)
  const id = chat.startAssistant()
  let done: DoneEvent | undefined

  try {
    const res = await fetch('/api/agent/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ query, conversation_id: chat.conversationId }),
    })

    if (res.status === 429) {
      const retryAfter = Number(res.headers.get('Retry-After')) || 60
      startRateLimitCountdown(retryAfter)
      chat.failAssistant(id, `Demasiadas consultas seguidas. Espera ${retryAfter} s y vuelve a intentarlo.`)
      return
    }
    if (!res.ok || !res.body) {
      let detail = `HTTP ${res.status}`
      try {
        const body = (await res.json()) as { detail?: unknown }
        if (typeof body.detail === 'string') detail = body.detail
      } catch {
        /* not JSON */
      }
      throw new Error(detail)
    }

    for await (const evt of readEvents(res.body)) {
      const c = useChat.getState()
      switch (evt.type) {
        case 'text':
          c.appendText(id, evt.delta)
          break
        case 'ui_resource':
          c.addWidget(id, evt)
          break
        case 'agent_init':
        case 'llm_call':
        case 'model_attempt':
        case 'tool_call':
        case 'tool_result':
          c.addTrace(id, evt)
          break
        case 'error':
          c.failAssistant(id, evt.message ?? 'Error del agente.')
          break
        case 'done':
          done = evt
          break
      }
    }
  } catch (err) {
    useChat.getState().failAssistant(id, err instanceof Error ? err.message : 'Error de red.')
  } finally {
    const c = useChat.getState()
    c.finishAssistant(id, done)
    c.setInFlight(false)
    running = false
  }
}
