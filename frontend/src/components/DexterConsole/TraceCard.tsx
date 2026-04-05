import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import type {
  LlmCallEvent,
  ModelAttemptEvent,
  SystemLogEvent,
  ToolCallEvent,
  ToolResultEvent,
  TraceEvent,
} from '../../types'
import { guessToolType } from '../../hooks/useAgentStream'
import { useWikiContent } from '../../hooks/useWikiContent'
import { JsonViewer, tryParseJson } from './JsonViewer'
import styles from './TraceCard.module.css'

// ── EduPanel ──────────────────────────────────────────────────────
// Single source of truth: pulls lesson content from /api/wiki/lessons,
// resolving the matching lesson via the events-catalog by event type.

function EduPanel({ eventType }: { eventType: string }) {
  const [open, setOpen] = useState(false)
  const { lessons, catalog } = useWikiContent()

  if (!lessons || !catalog) return null
  const entry = catalog[eventType]
  if (!entry?.related_lesson_id) return null
  const lesson = lessons.find((l) => l.id === entry.related_lesson_id)
  if (!lesson) return null

  return (
    <div className={styles.eduContainer}>
      <button className={styles.eduToggleBtn} onClick={() => setOpen((v) => !v)}>
        {open ? '[cerrar]' : `💡 ¿Cómo funciona esto? — ${lesson.title}`}
      </button>
      {open && (
        <div className={styles.eduContent}>
          <ReactMarkdown>{lesson.body_md}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

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
      </div>
      {preview && (
        <div className={styles.meta} style={{ fontStyle: 'italic' }}>
          &ldquo;{preview}{preview.length >= 100 ? '…' : ''}&rdquo;
        </div>
      )}
      <EduPanel eventType="llm_call" />
      <div className={styles.actionContainer}>
        <button
          className={styles.actionBtn}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '[cerrar]' : '🔍 ¿cómo funciona por dentro?'}
        </button>
        {open && (
          <div className={styles.expandable}>
            <JsonViewer data={messages} maxHeight="400px" />
          </div>
        )}
      </div>
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

// ── Skill Call ────────────────────────────────────────────────────

function SkillCallCard({ e }: { e: ToolCallEvent }) {
  const skillName = (e.args as Record<string, unknown>)?.skill_name as string ?? e.tool

  return (
    <div className={`${styles.card} ${styles.skill}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles.skill}`}>SKILL</span>
        <span className={styles.name}>{skillName}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
      </div>
      <div className={styles.meta}>
        Cargando instrucciones de skill para el agente Dexter...
      </div>
      <EduPanel eventType="tool_call" />
    </div>
  )
}

// ── Tool Call ─────────────────────────────────────────────────────

function ToolCallCard({ e }: { e: ToolCallEvent }) {
  const typeKey = guessToolType(e.tool)

  if (typeKey === 'skill') return <SkillCallCard e={e} />

  return (
    <div className={`${styles.card} ${styles[typeKey]}`}>
      <div className={styles.header}>
        <span className={`${styles.typeBadge} ${styles[typeKey]}`}>{typeKey.toUpperCase()}</span>
        <span className={styles.name}>{e.tool}</span>
        <span className={styles.timing}>+{e.timestamp_ms ?? 0}ms</span>
      </div>
      <JsonViewer data={e.args ?? {}} maxHeight="120px" />
      <EduPanel eventType="tool_call" />
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
        <>
          <div className={styles.meta} style={{ marginTop: 3, borderTop: '1px dashed var(--gbc-border)', paddingTop: 3 }}>
            <span>Resultado:</span> {full.slice(0, 300)}{full.length > 300 ? '…' : ''}
          </div>
          {full.length > 300 && (
            <div className={styles.actionContainer}>
              <button className={styles.actionBtn} onClick={() => setOpen((v) => !v)}>
                {open ? '[cerrar]' : '📄 ver todo'}
              </button>
              {open && (
                <div className={styles.expandable}>
                  <pre className={styles.resultFull}>{full}</pre>
                </div>
              )}
            </div>
          )}
        </>
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
