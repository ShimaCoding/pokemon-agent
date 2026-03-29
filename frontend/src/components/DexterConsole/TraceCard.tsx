import { useState } from 'react'
import type {
  LlmCallEvent,
  ModelAttemptEvent,
  SystemLogEvent,
  ToolCallEvent,
  ToolResultEvent,
  TraceEvent,
} from '../../types'
import { guessToolType } from '../../hooks/useAgentStream'
import { JsonViewer, tryParseJson } from './JsonViewer'
import styles from './TraceCard.module.css'

interface Props {
  event: TraceEvent
  /** For tool_result: reference to the matching tool_call card index */
  toolCallIndex?: number
}

// ── LLM Call ──────────────────────────────────────────────────────

function LlmCallCard({ e }: { e: LlmCallEvent }) {
  const [open, setOpen] = useState(false)
  const messages = e.messages ?? []
  const userMsg  = [...messages].reverse().find((m) => m.role === 'user')
  const preview  = (() => {
    if (!userMsg) return ''
    const raw = typeof userMsg.content === 'string'
      ? userMsg.content
      : JSON.stringify(userMsg.content)
    return raw.slice(0, 100)
  })()

  return (
    <div className={`${styles.card} ${styles.llm}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles.llm}`}>PROMPT</span>
        <span className={styles.name}>LLM Call #{e.call_index}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
        <button
          className={styles.toggleBtn}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '[ocultar]' : '[ver msgs]'}
        </button>
      </div>
      {preview && (
        <div className={styles.meta} style={{ fontStyle: 'italic' }}>
          &ldquo;{preview}{preview.length >= 100 ? '…' : ''}&rdquo;
        </div>
      )}
      {open && (
        <div className={styles.expandable}>
          {messages.map((m, i) => {
            const parsed = typeof m.content === 'string'
              ? tryParseJson(m.content)
              : m.content
            return (
              <div key={i}>
                <div className={styles.msgRole}>[{m.role}]</div>
                {parsed !== null && typeof parsed === 'object' ? (
                  <JsonViewer data={parsed} maxHeight="200px" />
                ) : (
                  <pre className={styles.msgContent}>
                    {typeof m.content === 'string' ? m.content : JSON.stringify(m.content, null, 2)}
                  </pre>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Model Attempt ─────────────────────────────────────────────────

function ModelAttemptCard({ e }: { e: ModelAttemptEvent }) {
  const typeKey = e.status === 'success' ? 'modelOk' : 'modelFail'
  const icon    = e.status === 'success' ? '✓' : '✗'

  return (
    <div className={`${styles.card} ${styles[typeKey]}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles[typeKey]}`}>LLM</span>
        <span className={styles.name}>{e.model ?? 'unknown'}</span>
        <span className={styles.timing}>{icon} +{e.timestamp_ms ?? 0}ms</span>
      </div>
      {e.error && (
        <div className={styles.meta} style={{ color: '#b84a4a' }}>{e.error}</div>
      )}
    </div>
  )
}

// ── Tool Call ─────────────────────────────────────────────────────

function ToolCallCard({ e }: { e: ToolCallEvent }) {
  const typeKey = guessToolType(e.tool)

  return (
    <div className={`${styles.card} ${styles[typeKey]}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles[typeKey]}`}>{typeKey.toUpperCase()}</span>
        <span className={styles.name}>{e.tool}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
      </div>
      <JsonViewer data={e.args ?? {}} maxHeight="120px" />
    </div>
  )
}

// ── Tool Result ───────────────────────────────────────────────────

function ToolResultCard({ e }: { e: ToolResultEvent }) {
  const [open, setOpen] = useState(false)
  const full    = String(e.result ?? '')
  const parsed  = tryParseJson(full)

  return (
    <div className={`${styles.card} ${styles.result}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles.result}`}>RESULT</span>
        <span className={styles.name}>Tool Result #{e.index}</span>
      </div>
      {parsed !== null ? (
        <JsonViewer data={parsed} maxHeight="160px" />
      ) : (
        <div className={styles.meta} style={{ marginTop: 3, borderTop: '1px dashed var(--gbc-border)', paddingTop: 3 }}>
          <span>Resultado:</span> {full.slice(0, 300)}{full.length > 300 ? '…' : ''}
          {full.length > 300 && (
            <>
              {' '}
              <button className={styles.toggleBtn} onClick={() => setOpen((v) => !v)}>
                {open ? '[ocultar]' : '[ver todo]'}
              </button>
              {open && (
                <div className={styles.expandable}>
                  <pre className={styles.resultFull}>{full}</pre>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── System Log ────────────────────────────────────────────────────

function SystemLogCard({ e }: { e: SystemLogEvent }) {
  return (
    <div className={`${styles.systemLog} ${styles[e.level]}`}>
      {e.message}
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────────

export default function TraceCard({ event }: Props) {
  switch (event.type) {
    case 'llm_call':      return <LlmCallCard      e={event as LlmCallEvent}      />
    case 'model_attempt': return <ModelAttemptCard e={event as ModelAttemptEvent} />
    case 'tool_call':     return <ToolCallCard      e={event as ToolCallEvent}     />
    case 'tool_result':   return <ToolResultCard   e={event as ToolResultEvent}   />
    case 'system_log':    return <SystemLogCard    e={event as SystemLogEvent}    />
    default:              return null
  }
}
