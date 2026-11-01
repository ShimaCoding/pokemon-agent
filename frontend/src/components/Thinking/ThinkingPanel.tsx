import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { useLessonFor } from '../../hooks/useWikiContent'
import type {
  AgentInitEvent,
  AssistantMessage,
  LlmCallEvent,
  ModelAttemptEvent,
  ToolCallEvent,
  ToolResultEvent,
  TraceEvent,
} from '../../types'
import JsonViewer from './JsonViewer'
import styles from './ThinkingPanel.module.css'

const shortModel = (model: string) => model.split('/').pop() ?? model

/** One-line status for the collapsed header while the reply streams. */
function liveStatus(traces: TraceEvent[]): string {
  const last = traces[traces.length - 1]
  if (!last) return 'Conectando con la Pokédex…'
  switch (last.type) {
    case 'agent_init':
      return 'Preparando herramientas…'
    case 'llm_call':
      return `Pensando con ${shortModel(last.model)}…`
    case 'model_attempt':
      return last.status === 'success' ? 'Pensando…' : `Falló ${shortModel(last.model)}, probando otro modelo…`
    case 'tool_call':
      return last.tool === 'skills' ? 'Cargando instrucciones de Dexter…' : `Consultando ${last.tool}…`
    case 'tool_result':
      return 'Analizando resultados…'
  }
}

function Lesson({ eventType }: { eventType: string }) {
  const [open, setOpen] = useState(false)
  const lesson = useLessonFor(eventType)
  if (!lesson) return null
  return (
    <div className={styles.lesson}>
      <button className={styles.link} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? 'Ocultar explicación' : `¿Cómo funciona? · ${lesson.title}`}
      </button>
      {open && (
        <div className={`md ${styles.lessonBody}`}>
          <ReactMarkdown>{lesson.body_md}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

function Details({ label, data }: { label: string; data: unknown }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button className={styles.link} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? 'Ocultar' : label}
      </button>
      {open && <JsonViewer data={data} />}
    </>
  )
}

function Step({ dot, title, meta, children }: { dot: string; title: React.ReactNode; meta?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <li className={styles.step}>
      <span className={styles.dot} data-kind={dot} aria-hidden="true" />
      <div className={styles.stepBody}>
        <div className={styles.stepHead}>
          <span className={styles.stepTitle}>{title}</span>
          {meta && <span className={styles.stepMeta}>{meta}</span>}
        </div>
        {children}
      </div>
    </li>
  )
}

function InitStep({ e }: { e: AgentInitEvent }) {
  return (
    <Step dot="init" title="Agente listo" meta={`+${e.timestamp_ms} ms`}>
      <p className={styles.detail}>
        {e.mcp_tools_count} herramientas · skill {e.skill_loaded ?? '—'}
        {e.history_messages > 0 && ` · recuerda ${e.history_messages} mensajes previos`}
      </p>
      <Lesson eventType="agent_init" />
    </Step>
  )
}

function LlmStep({ e, attempt }: { e: LlmCallEvent; attempt?: ModelAttemptEvent }) {
  const failed = attempt?.status === 'failed'
  return (
    <Step
      dot={failed ? 'fail' : 'llm'}
      title={<>Llamada al LLM #{e.call_index} · <code>{shortModel(e.model)}</code></>}
      meta={`+${e.timestamp_ms} ms`}
    >
      {failed && <p className={styles.error}>{attempt?.error}</p>}
      <Details label={`Ver los ${e.messages.length} mensajes enviados`} data={e.messages} />
      <Lesson eventType="llm_call" />
    </Step>
  )
}

function ToolStep({ call, result }: { call: ToolCallEvent; result?: ToolResultEvent }) {
  const isSkill = call.tool === 'skills'
  let parsed: unknown = result?.result
  if (result && !isSkill) {
    try {
      parsed = JSON.parse(result.result)
    } catch {
      /* plain text result */
    }
  }
  return (
    <Step
      dot={isSkill ? 'skill' : 'tool'}
      title={isSkill ? <>Skill · <code>{String(call.args.skill_name ?? 'dexter')}</code></> : <>Tool MCP · <code>{call.tool}</code></>}
      meta={`+${call.timestamp_ms} ms`}
    >
      {!isSkill && Object.keys(call.args).length > 0 && (
        <p className={styles.detail}>
          <code>{JSON.stringify(call.args)}</code>
        </p>
      )}
      {result ? (
        isSkill ? (
          <p className={styles.detail}>{result.result}</p>
        ) : (
          <Details label="Ver resultado" data={parsed} />
        )
      ) : (
        <p className={styles.detail}>Esperando resultado…</p>
      )}
      <Lesson eventType="tool_call" />
    </Step>
  )
}

function Steps({ traces }: { traces: TraceEvent[] }) {
  const results = new Map<string, ToolResultEvent>()
  for (const t of traces) if (t.type === 'tool_result') results.set(t.tool_id, t)

  const items: React.ReactNode[] = []
  traces.forEach((t, i) => {
    if (t.type === 'agent_init') items.push(<InitStep key={i} e={t} />)
    if (t.type === 'llm_call') {
      const attempt = traces.slice(i + 1).find((x): x is ModelAttemptEvent => x.type === 'model_attempt')
      items.push(<LlmStep key={i} e={t} attempt={attempt} />)
    }
    if (t.type === 'tool_call') items.push(<ToolStep key={i} call={t} result={results.get(t.tool_id)} />)
  })
  return <ol className={styles.steps}>{items}</ol>
}

function Summary({ message }: { message: AssistantMessage }) {
  const d = message.done
  if (!d) return null
  const models = d.models_tried.map(shortModel).join(' → ') || '—'
  return (
    <p className={styles.summary}>
      {(d.elapsed_ms / 1000).toFixed(1)} s · {models} · {d.total_tool_calls ?? 0} tools
      {d.total_tokens ? ` · ${d.total_tokens.toLocaleString('es')} tokens (↑${d.input_tokens?.toLocaleString('es')} ↓${d.output_tokens?.toLocaleString('es')})` : ''}
    </p>
  )
}

/** Collapsible "ver cómo lo pensó": LLM calls, tools, model and tokens for one reply. */
export default function ThinkingPanel({ message }: { message: AssistantMessage }) {
  const [open, setOpen] = useState(false)
  const streaming = message.status === 'streaming'
  const label = streaming ? liveStatus(message.traces) : 'Ver cómo lo pensó'

  if (!streaming && message.traces.length === 0) return null

  return (
    <div className={styles.panel} data-open={open}>
      <button className={styles.toggle} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {streaming && <span className={styles.spinner} aria-hidden="true" />}
        <span className={streaming ? styles.live : undefined}>{label}</span>
        <span className={styles.chevron} aria-hidden="true">›</span>
      </button>
      {open && (
        <div className={styles.body}>
          <Steps traces={message.traces} />
          <Summary message={message} />
        </div>
      )}
    </div>
  )
}
