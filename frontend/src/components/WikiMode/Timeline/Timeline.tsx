import { useEffect, useRef } from 'react'
import styles from './Timeline.module.css'
import TimelineEvent from './TimelineEvent'
import type { EventsCatalog, TraceEvent } from '../../../types'

interface Props {
  traceLogs: TraceEvent[]
  catalog: EventsCatalog
  onEventClick?: (eventType: string) => void
}

// Don't render deltas directly — they'd flood the list. Keep traces meaningful.
const RENDER_TYPES = new Set([
  'start',
  'agent_init',
  'llm_call',
  'model_attempt',
  'tool_call',
  'tool_result',
  'done',
  'error',
  'system_log',
])

export default function Timeline({ traceLogs, onEventClick }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const filtered = traceLogs.filter((e) => RENDER_TYPES.has(e.type))

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [filtered.length])

  return (
    <div className={styles.timeline}>
      <div className={styles.header}>Timeline ({filtered.length})</div>
      <div className={styles.list}>
        {filtered.length === 0 ? (
          <div className={styles.empty}>
            Lanzá una query para ver los eventos del agente aquí.
          </div>
        ) : (
          filtered.map((event, i) => (
            <TimelineEvent key={i} event={event} onClick={onEventClick} />
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
