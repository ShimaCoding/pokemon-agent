import { useId, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import type { TraceEvent } from '../../types'
import { useLessonFor } from '../../hooks/useWikiContent'
import { tryParseJson } from '../../lib/json'
import { JsonViewer } from './JsonViewer'
import { argSummary, buildSteps, formatMs, shortModel, type Step, type ToolRun } from './steps'
import styles from './TraceTimeline.module.css'

// ── Lesson link ───────────────────────────────────────────────────
// Lesson content comes from /api/wiki/lessons, resolved by event type.

function LessonLink({ eventType }: { eventType: string }) {
  const [open, setOpen] = useState(false)
  const lesson = useLessonFor(eventType)
  if (!lesson) return null

  return (
    <div className={styles.lesson}>
      <button className={styles.lessonToggle} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? '▾' : '▸'} ¿Cómo funciona? — {lesson.title}
      </button>
      {open && (
        <div className={styles.lessonBody}>
          <ReactMarkdown>{lesson.body_md}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

// ── Step summaries ────────────────────────────────────────────────

interface Summary {
  icon: string
  tone: 'init' | 'think' | 'tool' | 'skill' | 'fail'
  label: string
  sub?: string
  pending: boolean
}

function summarize(step: Step, next: Step | undefined, finished: boolean): Summary {
  switch (step.kind) {
    case 'init': {
      const e = step.event
      const sub = [
        e.skill_loaded && `skill ${e.skill_loaded}`,
        e.history_messages > 0 && `recuerda ${e.history_messages} mensajes`,
      ].filter(Boolean).join(' · ')
      return { icon: '●', tone: 'init', label: `Preparó ${e.mcp_tools_count} herramientas`, sub, pending: false }
    }

    case 'think': {
      const current = step.attempts[step.attempts.length - 1]
      const model = shortModel(current.model)
      if (current.status === 'failed') {
        return { icon: '✗', tone: 'fail', label: `Falló ${model}`, sub: current.error, pending: false }
      }
      const pending = current.status === 'pending'
      const label = pending
        ? 'Pensando…'
        : next
          ? 'Decidió qué consultar'
          : finished ? 'Escribió la respuesta' : 'Pensó'
      // With failovers the row shows the whole model chain instead.
      const sub = step.attempts.length > 1 ? undefined : model
      return { icon: '◆', tone: 'think', label, sub, pending }
    }

    case 'tools': {
      const tools = new Set(step.runs.map((r) => r.call.tool))
      const pending = step.runs.some((r) => !r.result)
      const count = step.runs.length > 1 ? ` ×${step.runs.length}` : ''
      if (tools.size === 1) {
        const sub = step.runs.map((r) => argSummary(r.call.args)).filter(Boolean).join(' · ')
        return { icon: '■', tone: 'tool', label: `Consultó ${step.runs[0].call.tool}${count}`, sub, pending }
      }
      return { icon: '■', tone: 'tool', label: `Usó ${tools.size} herramientas${count}`, sub: [...tools].join(' · '), pending }
    }

    case 'skill': {
      const name = (step.run.call.args?.skill_name as string | undefined) ?? step.run.call.tool
      return { icon: '✦', tone: 'skill', label: `Cargó la skill ${name}`, pending: !step.run.result }
    }
  }
}

// ── Step details ──────────────────────────────────────────────────

const RESULT_PREVIEW = 400

function ResultView({ result }: { result: string }) {
  const [full, setFull] = useState(false)
  const parsed = tryParseJson(result)
  if (parsed !== null) return <JsonViewer data={parsed} maxHeight="240px" />

  const long = result.length > RESULT_PREVIEW
  return (
    <>
      <pre className={styles.text}>{full || !long ? result : `${result.slice(0, RESULT_PREVIEW)}…`}</pre>
      {long && (
        <button className={styles.more} onClick={() => setFull((v) => !v)}>
          {full ? 'ver menos' : 'ver todo'}
        </button>
      )}
    </>
  )
}

function RunDetail({ run }: { run: ToolRun }) {
  return (
    <div className={styles.run}>
      <div className={styles.runName}>{run.call.tool}</div>
      <div className={styles.field}>Entrada</div>
      <JsonViewer data={run.call.args ?? {}} maxHeight="120px" />
      <div className={styles.field}>Resultado</div>
      {run.result ? <ResultView result={String(run.result.result ?? '')} /> : <p className={styles.muted}>Esperando…</p>}
    </div>
  )
}

function StepDetail({ step }: { step: Step }) {
  switch (step.kind) {
    case 'init': {
      const e = step.event
      return (
        <>
          <div className={styles.field}>Herramientas disponibles</div>
          <div className={styles.chips}>
            {e.tool_names.map((name) => <span key={name} className={styles.chip}>{name}</span>)}
          </div>
          {e.system_prompt_preview && (
            <>
              <div className={styles.field}>System prompt</div>
              <pre className={styles.text}>{e.system_prompt_preview}</pre>
            </>
          )}
          <LessonLink eventType="agent_init" />
        </>
      )
    }

    case 'think': {
      const current = step.attempts[step.attempts.length - 1]
      return (
        <>
          {step.attempts.length > 1 && (
            <>
              <div className={styles.field}>Intentos (fallback entre proveedores)</div>
              <ol className={styles.attempts}>
                {step.attempts.map((a, i) => (
                  <li key={i} data-status={a.status}>
                    {a.status === 'failed' ? '✗' : a.status === 'success' ? '✓' : '…'} {shortModel(a.model)}
                    {a.error && <span className={styles.muted}> — {a.error}</span>}
                  </li>
                ))}
              </ol>
            </>
          )}
          <div className={styles.field}>Mensajes enviados a {shortModel(current.model)}</div>
          <JsonViewer data={current.messages} maxHeight="280px" />
          <LessonLink eventType="llm_call" />
        </>
      )
    }

    case 'tools':
      return (
        <>
          {step.runs.map((run) => <RunDetail key={run.call.tool_id} run={run} />)}
          <LessonLink eventType="tool_call" />
        </>
      )

    case 'skill':
      return (
        <>
          <RunDetail run={step.run} />
          <LessonLink eventType="tool_call" />
        </>
      )
  }
}

// ── Timeline ──────────────────────────────────────────────────────

interface Props {
  traces: TraceEvent[]
  finished: boolean
}

/** One row per step; clicking a row reveals its inputs, outputs and lesson. */
export default function TraceTimeline({ traces, finished }: Props) {
  const steps = useMemo(() => buildSteps(traces), [traces])
  const [openIdx, setOpenIdx] = useState<number | null>(null)
  const baseId = useId()

  return (
    <ol className={styles.timeline}>
      {steps.map((step, i) => {
        const s = summarize(step, steps[i + 1], finished)
        const open = openIdx === i
        const detailId = `${baseId}-${i}`
        return (
          <li key={i} className={styles.step} data-tone={s.tone} data-open={open}>
            <button
              className={styles.row}
              onClick={() => setOpenIdx(open ? null : i)}
              aria-expanded={open}
              aria-controls={detailId}
            >
              <span className={styles.icon} data-pending={s.pending} aria-hidden>{s.icon}</span>
              <span className={styles.label}>{s.label}</span>
              {s.sub && <span className={styles.sub}>{s.sub}</span>}
              {step.kind === 'think' && step.attempts.length > 1 && (
                <span className={styles.fallback}>
                  {step.attempts.map((a) => `${shortModel(a.model)} ${a.status === 'failed' ? '✗' : '✓'}`).join(' → ')}
                </span>
              )}
              <span className={styles.time}>+{formatMs(step.at ?? 0)}</span>
              <span className={styles.chevron} aria-hidden>{open ? '▾' : '▸'}</span>
            </button>
            {open && (
              <div id={detailId} className={styles.detail}>
                <StepDetail step={step} />
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
