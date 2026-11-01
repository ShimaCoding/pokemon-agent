import { useEffect, useState } from 'react'
import type { AssistantMessage, TraceEvent } from '../../types'
import TraceCard from '../Trace/TraceCard'
import TraceSummary from '../Trace/TraceSummary'
import styles from './ThinkingPanel.module.css'

const LOADING_PHRASES = [
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

/** What the agent is doing right now, from the latest trace event. */
function liveStep(traces: TraceEvent[]): string | null {
  const last = traces[traces.length - 1]
  if (!last) return null
  switch (last.type) {
    case 'agent_init':
      return 'preparando herramientas'
    case 'llm_call':
      return `pensando con ${last.model.split('/').pop()}`
    case 'model_attempt':
      return last.status === 'success' ? 'pensando' : `falló ${last.model.split('/').pop()}, probando de nuevo`
    case 'tool_call':
      return last.tool === 'skills' ? 'cargando instrucciones de Dexter' : `consultando ${last.tool}`
    case 'tool_result':
      return 'analizando resultados'
  }
}

/** Collapsible "ver cómo lo pensó": the old Dev console, one per reply. */
export default function ThinkingPanel({ message }: { message: AssistantMessage }) {
  const [open, setOpen] = useState(false)
  const [phraseIdx, setPhraseIdx] = useState(() => Math.floor(Math.random() * LOADING_PHRASES.length))
  const streaming = message.status === 'streaming'

  useEffect(() => {
    if (!streaming) return
    const id = setInterval(() => setPhraseIdx((i) => (i + 1) % LOADING_PHRASES.length), 2500)
    return () => clearInterval(id)
  }, [streaming])

  if (!streaming && message.traces.length === 0) return null

  const toolCalls = message.traces.filter((t) => t.type === 'tool_call').length
  const step = liveStep(message.traces)

  return (
    <div className={styles.panel}>
      <button className={styles.toggle} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={styles.arrow}>{open ? '▼' : '▶'}</span>
        {streaming ? (
          <span className={styles.live}>
            <span className={styles.cursor}>█</span> {LOADING_PHRASES[phraseIdx]}
            {step && <span className={styles.step}> [{step}]</span>}
          </span>
        ) : (
          <span>
            Ver cómo lo pensó
            <span className={styles.count}> ({message.traces.length} eventos)</span>
          </span>
        )}
      </button>
      {open && (
        <div className={styles.console}>
          {message.traces.map((t, i) => (
            <TraceCard key={i} event={t} />
          ))}
          {message.done && <TraceSummary event={message.done} toolCallCount={toolCalls} />}
        </div>
      )}
    </div>
  )
}
