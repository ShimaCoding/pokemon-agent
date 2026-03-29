import { useRef, useCallback } from 'react'
import useStore from '../store/useStore'
import { fetchPokemonStructured } from './usePokeAPI'
import type {
  DoneEvent,
  LlmCallEvent,
  ModelAttemptEvent,
  ToolCallEvent,
  ToolResultEvent,
} from '../types'

// ── Loading phrases ───────────────────────────────────────────────

export const LOADING_PHRASES = [
  'Conectando con el PC de Bill...',
  'Despertando al Snorlax del servidor...',
  'Analizando espécimen (y tus nulas habilidades)...',
  'Alimentando a los Porygon del sistema...',
  'Buscando algo que deberías haber estudiado...',
  'Calculando si este Pokémon te desobedecerá...',
  'Traduciendo antiguos textos Unown...',
  'Sincronizando con el Profesor Oak...',
  'Girando los engranajes de Klinklang...',
  'Procesando... ten paciencia de Maestro Pokémon.',
]

export function getRandomLoadingPhrase(): string {
  return LOADING_PHRASES[Math.floor(Math.random() * LOADING_PHRASES.length)]
}

// ── Pokemon name extraction helpers ──────────────────────────────

const NON_POKEMON = new Set([
  'prompt','battle','matchup','analysis','educational','template',
  'usando','utiliza','muestra','dame','dime','sobre','consulta',
  'agente','agent','modelo','model','recurso','resource','tipo',
  'type','nombre','name','lista','list','info','datos','data',
  'genera','generate','crear','create','cual','cuales','como',
  'para','entre','versus','contra','with','from','that','this',
  'what','which','give','show','tell','find','get','make',
])

export function extractPokemonName(query: string): string | null {
  const q = query.toLowerCase()
  const match = q.match(/(?:de|sobre|el|la|pokemon|pokémon)\s+([a-záéíóúñ]+)/i)
  if (match && match[1] && !NON_POKEMON.has(match[1])) return match[1]
  const words = q.split(/\s+/).filter((w) => w.length > 3 && !NON_POKEMON.has(w))
  return words[words.length - 1] ?? null
}

const KNOWN_POKEMON = [
  'pikachu','charizard','blastoise','venusaur','mewtwo','gengar',
  'snorlax','eevee','meowth','raichu','bulbasaur','charmander','squirtle',
  'jigglypuff','psyduck','machamp','gyarados','lapras','ditto','vaporeon',
  'jolteon','flareon','alakazam','machoke','geodude','slowpoke','magikarp',
]

export function detectPokemonInText(text: string): string | null {
  const lower = text.toLowerCase()
  return KNOWN_POKEMON.find((n) => lower.includes(n)) ?? null
}

// ── Tool type guesser ─────────────────────────────────────────────

export function guessToolType(toolName: string): string {
  if (!toolName) return 'tool'
  if (toolName.includes('resource') || toolName.startsWith('pokemon://')) return 'resource'
  if (toolName.includes('prompt') || toolName.includes('educational'))    return 'prompt'
  return 'tool'
}

// ── Hook ──────────────────────────────────────────────────────────

export function useAgentStream() {
  const inFlightRef = useRef(false)

  const apiKey          = useStore((s) => s.apiKey)
  const selectedProvider = useStore((s) => s.selectedProvider)
  const setInFlight     = useStore((s) => s.setInFlight)
  const resetSession    = useStore((s) => s.resetSession)
  const appendText      = useStore((s) => s.appendText)
  const appendTraceLog  = useStore((s) => s.appendTraceLog)
  const setPokemonData  = useStore((s) => s.setPokemonData)
  const setPreQuery     = useStore((s) => s.setPreQuery)

  const runQuery = useCallback(
    async (query: string) => {
      if (inFlightRef.current) return
      inFlightRef.current = true
      setInFlight(true)
      setPreQuery(false)
      resetSession()

      const pokemonFromQuery = extractPokemonName(query)

      appendTraceLog({
        type: 'system_log',
        message: `▶ Query: "${query.slice(0, 60)}${query.length > 60 ? '…' : ''}"`,
        level: 'info',
      })

      try {
        const headers: Record<string, string> = { 'Content-Type': 'application/json' }
        if (apiKey) headers['X-API-Key'] = apiKey

        const res = await fetch('/api/agent/run', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            query,
            provider: selectedProvider || null,
          }),
        })

        if (!res.ok && res.status !== 200) {
          const errBody = await res.json().catch(() => ({ detail: `Error ${res.status}` })) as { detail?: string }
          throw new Error(errBody.detail ?? `Error ${res.status}`)
        }

        const reader  = res.body!.getReader()
        const decoder = new TextDecoder()
        let   buffer  = ''
        let   fullText = ''
        let   toolCallsSinceLastLlm = false

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const parts = buffer.split('\n\n')
          buffer = parts.pop() ?? ''

          for (const part of parts) {
            const line = part.trim()
            if (!line.startsWith('data:')) continue
            let evt: Record<string, unknown>
            try { evt = JSON.parse(line.slice(5).trim()) as Record<string, unknown> }
            catch { continue }

            switch (evt['type']) {
              case 'start':
                appendTraceLog({
                  type: 'system_log',
                  message: `⚡ Provider: ${evt['provider'] as string}`,
                  level: 'info',
                })
                break
              case 'llm_call': {
                if (toolCallsSinceLastLlm) {
                  appendTraceLog({
                    type: 'system_log',
                    message: '📝 Procesando resultados → formulando respuesta…',
                    level: 'info',
                  })
                  toolCallsSinceLastLlm = false
                }
                appendTraceLog(evt as unknown as LlmCallEvent)
                break
              }
              case 'model_attempt': {
                const mae = evt as unknown as ModelAttemptEvent
                appendTraceLog(mae)
                if (mae.status !== 'success') {
                  const shortName = mae.model.split('/').pop() ?? mae.model
                  appendTraceLog({
                    type: 'system_log',
                    message: `⚠ ${shortName} no disponible → probando fallback…`,
                    level: 'warn',
                  })
                }
                break
              }
              case 'tool_call': {
                if (!toolCallsSinceLastLlm) {
                  appendTraceLog({
                    type: 'system_log',
                    message: '🔧 El agente consulta herramientas MCP…',
                    level: 'info',
                  })
                }
                toolCallsSinceLastLlm = true
                appendTraceLog(evt as unknown as ToolCallEvent)
                break
              }
              case 'tool_result':
                appendTraceLog(evt as unknown as ToolResultEvent)
                break
              case 'text': {
                const delta = (evt['delta'] as string) ?? ''
                fullText += delta
                appendText(delta)
                break
              }
              case 'done':
                appendTraceLog(evt as unknown as DoneEvent)
                break
              case 'error':
                appendTraceLog({ type: 'error', message: evt['message'] as string | undefined })
                break
            }
          }
        }

        // After stream: hydrate Pokémon panel from PokeAPI
        const detectedName = pokemonFromQuery ?? detectPokemonInText(fullText)
        if (detectedName) {
          const data = await fetchPokemonStructured(detectedName)
          if (data) setPokemonData(data)
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Error desconocido'
        const isRateLimit =
          msg.toLowerCase().includes('demasiadas') ||
          msg.includes('429') ||
          msg.toLowerCase().includes('too many')
        const displayDelta = isRateLimit
          ? '⚡ ¡Pi-ka-pi! ¡Pika pika chu! *(Demasiadas consultas... El Maestro Pokémon dice: espera un momento antes de volver a intentarlo.)*'
          : `❌ *${msg}*`
        appendText(displayDelta)
        appendTraceLog({ type: 'error', message: msg })
      } finally {
        inFlightRef.current = false
        setInFlight(false)
      }
    },
    [
      apiKey,
      selectedProvider,
      setInFlight,
      resetSession,
      appendText,
      appendTraceLog,
      setPokemonData,
      setPreQuery,
    ]
  )

  return { runQuery }
}
