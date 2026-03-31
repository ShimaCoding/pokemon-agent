import { useEffect, useRef, useState } from 'react'
import useStore from '../../store/useStore'
import TraceCard from './TraceCard'
import TraceSummary from './TraceSummary'
import type { DoneEvent, ToolCallEvent } from '../../types'
import { LOADING_PHRASES } from '../../hooks/useAgentStream'
import styles from './DexterConsole.module.css'

interface Props {
  collapsible?: boolean
}

export default function DexterConsole({ collapsible = false }: Props) {
  const traceLogs     = useStore((s) => s.traceLogs)
  const inFlight      = useStore((s) => s.inFlight)
  const visibleCount  = useStore((s) => s.visibleTraceCount)
  const setVisibleCount = useStore((s) => s.setVisibleTraceCount)
  const fastForward     = useStore((s) => s.fastForward)
  const activeTab     = useStore((s) => s.activeTab)
  const setActiveTab  = useStore((s) => s.setActiveTab)
  const devMode       = useStore((s) => s.devMode)

  const bottomRef   = useRef<HTMLDivElement>(null)
  const [phraseIdx, setPhraseIdx] = useState(() => Math.floor(Math.random() * LOADING_PHRASES.length))
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    if (!inFlight) return
    const id = setInterval(() => {
      setPhraseIdx((i) => (i + 1) % LOADING_PHRASES.length)
    }, 2500)
    return () => clearInterval(id)
  }, [inFlight])

  const renderableLogs = traceLogs.filter(
    (e) => e.type !== 'text' && e.type !== 'done' && e.type !== 'error'
  )

  useEffect(() => {
    if (visibleCount < renderableLogs.length) {
      if (!inFlight || fastForward) {
        // La consulta finalizó o el usuario quiere adelantarlo a máxima velocidad
        setVisibleCount(renderableLogs.length)
      } else {
        const id = setTimeout(() => {
          setVisibleCount((c) => Math.min(c + 1, renderableLogs.length))
        }, 500)
        return () => clearTimeout(id)
      }
    }
  }, [visibleCount, renderableLogs.length, inFlight, fastForward, setVisibleCount])

  const visibleLogs = renderableLogs.slice(0, visibleCount)

  const toolCallCount = traceLogs.filter((e) => e.type === 'tool_call').length
  const doneEvent     = traceLogs.find((e) => e.type === 'done') as DoneEvent | undefined

  // Cambio automático de pestaña cuando la consola termina su animación
  const hasAutoSwitched = useRef(false)

  // Reseteamos el flag para cada nueva consulta
  useEffect(() => {
    if (inFlight) hasAutoSwitched.current = false
  }, [inFlight])

  useEffect(() => {
    // Si la consola mostró todos los items, existe el doneEvent (lo que significa que terminó).
    if (doneEvent && visibleCount >= renderableLogs.length && devMode && !hasAutoSwitched.current) {
      // Si estamos en consola, esperamos los 2s pacientemente
      if (activeTab === 'consola') {
        const id = setTimeout(() => {
          hasAutoSwitched.current = true
          setActiveTab('dexter')
        }, 2000)
        
        // Si el usuario cambia manualmente de pestaña antes de los 2s, 
        // abortamos el timer y marcamos que "ya se hizo cargo"
        return () => {
          hasAutoSwitched.current = true
          clearTimeout(id)
        }
      } else {
        // Estaba en otra pestaña al finalizar, no intervenimos pero marcamos como switch resuelto
        hasAutoSwitched.current = true
      }
    }
  }, [doneEvent, visibleCount, renderableLogs.length, activeTab, devMode, setActiveTab])

  // Auto-scroll to bottom on new log (skip on initial mount when there are no logs)
  useEffect(() => {
    if (visibleLogs.length === 0) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [visibleLogs.length])

  const statsText = doneEvent
    ? `${toolCallCount} llamadas · ${doneEvent.elapsed_ms}ms${
        doneEvent.models_tried?.length
          ? ' · ' + doneEvent.models_tried.map((m) => m.split('/').pop()).join('→')
          : ''
      }`
    : inFlight
      ? LOADING_PHRASES[phraseIdx]
      : 'esperando…'

  return (
    <>
      <div
        id="trace-header"
        className={`${styles.traceHeader} ${collapsible ? styles.collapsible : ''}`}
        onClick={collapsible ? () => setCollapsed((c) => !c) : undefined}
        role={collapsible ? 'button' : undefined}
        aria-expanded={collapsible ? !collapsed : undefined}
      >
        <div className={styles.traceTitle}>Consola de Dexter (Logs)</div>
        <div className={styles.traceRight}>
          <div className={styles.traceStats} id="trace-stats">{statsText}</div>
          {collapsible && (
            <span className={`${styles.chevron} ${collapsed ? styles.chevronCollapsed : ''}`}>▲</span>
          )}
        </div>
      </div>
      {!collapsed && (
        <div id="trace-list" className={styles.traceList}>
          {renderableLogs.length === 0 && !inFlight && (
            <div className={styles.empty} id="trace-empty-msg">
              ejecuta una consulta<br />para ver la traza MCP
            </div>
          )}
          {inFlight && renderableLogs.length === 0 && (
            <div className={styles.empty}>Esperando herramientas…</div>
          )}

          {visibleLogs.map((e, i) => {
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

          {doneEvent && visibleCount >= renderableLogs.length && (
            <TraceSummary event={doneEvent} toolCallCount={toolCallCount} />
          )}

          <div ref={bottomRef} />
        </div>
      )}
    </>
  )
}
