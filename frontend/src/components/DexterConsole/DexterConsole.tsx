import { useEffect, useRef, useState } from 'react'
import useStore from '../../store/useStore'
import TraceCard from './TraceCard'
import TraceSummary from './TraceSummary'
import type { DoneEvent, ToolCallEvent } from '../../types'
import { LOADING_PHRASES } from '../../hooks/useAgentStream'
import styles from './DexterConsole.module.css'

export default function DexterConsole() {
  const traceLogs   = useStore((s) => s.traceLogs)
  const inFlight    = useStore((s) => s.inFlight)
  const bottomRef   = useRef<HTMLDivElement>(null)
  const [phraseIdx, setPhraseIdx] = useState(() => Math.floor(Math.random() * LOADING_PHRASES.length))

  useEffect(() => {
    if (!inFlight) return
    const id = setInterval(() => {
      setPhraseIdx((i) => (i + 1) % LOADING_PHRASES.length)
    }, 2500)
    return () => clearInterval(id)
  }, [inFlight])

  // Auto-scroll to bottom on new log
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [traceLogs.length])

  const toolCallCount = traceLogs.filter((e) => e.type === 'tool_call').length
  const doneEvent     = traceLogs.find((e) => e.type === 'done') as DoneEvent | undefined

  const statsText = doneEvent
    ? `${toolCallCount} llamadas · ${doneEvent.elapsed_ms}ms${
        doneEvent.models_tried?.length
          ? ' · ' + doneEvent.models_tried.map((m) => m.split('/').pop()).join('→')
          : ''
      }`
    : inFlight
      ? LOADING_PHRASES[phraseIdx]
      : 'esperando…'

  const renderableLogs = traceLogs.filter(
    (e) => e.type !== 'text' && e.type !== 'done' && e.type !== 'error'
  )

  return (
    <>
      <div id="trace-header" className={styles.traceHeader}>
        <div className={styles.traceTitle}>Consola de Dexter (Logs)</div>
        <div className={styles.traceStats} id="trace-stats">{statsText}</div>
      </div>
      <div id="trace-list" className={styles.traceList}>
        {renderableLogs.length === 0 && !inFlight && (
          <div className={styles.empty} id="trace-empty-msg">
            ejecuta una consulta<br />para ver la traza MCP
          </div>
        )}
        {inFlight && renderableLogs.length === 0 && (
          <div className={styles.empty}>Esperando herramientas…</div>
        )}

        {renderableLogs.map((e, i) => {
          // Pair tool_result with its matching tool_call
          if (e.type === 'tool_call') {
            const result = traceLogs.find(
              (r) => r.type === 'tool_result' && (r as { index: number }).index === (e as ToolCallEvent).index
            )
            return (
              <div key={i}>
                <TraceCard event={e} />
                {result && <TraceCard event={result} />}
              </div>
            )
          }
          if (e.type === 'tool_result') return null // already rendered above
          return <TraceCard key={i} event={e} />
        })}

        {doneEvent && (
          <TraceSummary event={doneEvent} toolCallCount={toolCallCount} />
        )}

        <div ref={bottomRef} />
      </div>
    </>
  )
}
