import { useState } from 'react'
import type {
  LlmCallEvent,
  ModelAttemptEvent,
  ToolCallEvent,
  ToolResultEvent,
  TraceEvent,
} from '../../types'
import { guessToolType } from '../../hooks/useAgentStream'
import styles from './TraceCard.module.css'

interface Props {
  event: TraceEvent
  /** For tool_result: reference to the matching tool_call card index */
  toolCallIndex?: number
}

function escHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
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
          {messages.map((m, i) => (
            <div key={i}>
              <div className={styles.msgRole}>[{m.role}]</div>
              <pre className={styles.msgContent}>
                {typeof m.content === 'string' ? m.content : JSON.stringify(m.content, null, 2)}
              </pre>
            </div>
          ))}
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
  const argsJson = JSON.stringify(e.args ?? {}, null, 2)

  return (
    <div className={`${styles.card} ${styles[typeKey]}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles[typeKey]}`}>{typeKey.toUpperCase()}</span>
        <span className={styles.name}>{e.tool}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
      </div>
      <pre className={styles.traceJson}>{escHtml(argsJson)}</pre>
    </div>
  )
}

// ── Tool Result ───────────────────────────────────────────────────

function ToolResultCard({ e }: { e: ToolResultEvent }) {
  const [open, setOpen] = useState(false)
  const full    = String(e.result ?? '')
  const preview = full.slice(0, 300)
  const hasMore = full.length > 300

  return (
    <div className={`${styles.card} ${styles.result}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles.result}`}>RESULT</span>
        <span className={styles.name}>Tool Result #{e.index}</span>
      </div>
      <div className={styles.meta} style={{ marginTop: 3, borderTop: '1px dashed var(--gbc-border)', paddingTop: 3 }}>
        <span>Resultado:</span> {preview}{hasMore ? '…' : ''}
        {hasMore && (
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
    default:              return null
  }
}
