import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { JsonViewer } from '../components/Trace/JsonViewer'
import { getTrace, hasKey, listTraces, saveKey, Unauthorized } from './api'
import { buildFlow, formatDuration, formatTokens, totalTokens, type FlowNode } from './layout'
import styles from './TracesApp.module.css'
import type { ChatMessage, TraceDetail, TraceSpan, TraceSummary } from './types'

const KIND_LABEL = { agent: 'Agente', llm: 'Modelo', tool: 'Tool' } as const

function clock(iso: string): string {
  return new Date(iso).toLocaleString('es-CL', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** One line that says what the step did, shown on its card. */
function summary(span: TraceSpan): string {
  if (span.error && span.errorMessage) return span.errorMessage
  if (span.kind === 'llm') {
    if (span.toolCalls?.length) return `Pide ${span.toolCalls.join(' y ')}`
    return span.output ? `Responde: ${span.output}` : 'Sin respuesta'
  }
  if (span.kind === 'tool') return JSON.stringify(span.input ?? {})
  return typeof span.input === 'string' ? span.input : ''
}

// ── The bar that places a step on the query's clock ────────────────

function TimeBar({ span, total }: { span: TraceSpan; total: number }) {
  const left = (span.startMs / total) * 100
  const width = Math.max((span.durationMs / total) * 100, 1.5)
  return (
    <span className={styles.bar} aria-hidden="true">
      <span className={styles.barFill} style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />
    </span>
  )
}

// ── Flow ────────────────────────────────────────────────────────────

interface FlowProps {
  node: FlowNode
  total: number
  selected: string
  onSelect: (id: string) => void
}

function StepCard({ node, total, selected, onSelect }: FlowProps) {
  const { span } = node
  const tokens = totalTokens(span)
  return (
    <button
      type="button"
      className={styles.card}
      data-kind={span.kind}
      data-error={span.error}
      aria-pressed={selected === span.id}
      onClick={() => onSelect(span.id)}
    >
      <span className={styles.cardHead}>
        <span className={styles.kind}>{KIND_LABEL[span.kind]}</span>
        <span className={styles.duration}>{formatDuration(span.durationMs)}</span>
      </span>
      <span className={styles.name}>{span.name}</span>
      <span className={styles.summary}>{summary(span)}</span>
      <TimeBar span={span} total={total} />
      <span className={styles.cardFoot}>
        <span>a los {formatDuration(span.startMs)}</span>
        {tokens !== null && <span>{formatTokens(tokens)} tokens</span>}
        {span.metadata?.cache === 'hit' && <span className={styles.chip}>caché</span>}
        {span.error && <span className={styles.chipError}>error</span>}
      </span>
    </button>
  )
}

function AgentFrame({ node, total, selected, onSelect }: FlowProps) {
  const { span } = node
  const tokens = totalTokens(span)
  return (
    <section className={styles.frame} data-error={span.error} data-selected={selected === span.id}>
      <button type="button" className={styles.frameHead} aria-pressed={selected === span.id} onClick={() => onSelect(span.id)}>
        <span className={styles.frameName}>{span.name}</span>
        <span className={styles.frameMeta}>
          {formatDuration(span.durationMs)}
          {tokens !== null && <> · {formatTokens(tokens)} tokens</>}
        </span>
        {span.error && <span className={styles.chipError}>error</span>}
        <TimeBar span={span} total={total} />
      </button>
      <ol className={styles.rows}>
        {node.rows.map((row, i) => (
          <li key={row[0].span.id} className={styles.row} data-parallel={row.length > 1}>
            {i > 0 && <span className={styles.arrow} aria-hidden="true" />}
            {row.length > 1 && <span className={styles.parallelNote}>{row.length} a la vez</span>}
            <div className={styles.branches} data-joins={i < node.rows.length - 1}>
              {row.map((child) => (
                <div key={child.span.id} className={styles.branch}>
                  <FlowItem node={child} total={total} selected={selected} onSelect={onSelect} />
                  <span className={styles.tail} aria-hidden="true" />
                </div>
              ))}
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

function FlowItem(props: FlowProps) {
  return props.node.span.kind === 'agent' ? <AgentFrame {...props} /> : <StepCard {...props} />
}

// ── Detail of the selected step ────────────────────────────────────

function isMessages(input: TraceSpan['input']): input is ChatMessage[] {
  return Array.isArray(input) && input.every((m) => typeof m === 'object' && m !== null && 'role' in m)
}

/** Tool results are usually JSON: show them as a tree when they parse. */
function parseJson(text: string): object | null {
  try {
    const value: unknown = JSON.parse(text)
    return typeof value === 'object' && value !== null ? value : null
  } catch {
    return null
  }
}

function Messages({ messages }: { messages: ChatMessage[] }) {
  return (
    <ol className={styles.messages}>
      {messages.map((m, i) => (
        <li key={i} data-role={m.role}>
          <span className={styles.role}>{m.role}</span>
          <p>{m.content}</p>
        </li>
      ))}
    </ol>
  )
}

function Detail({ span, trace }: { span: TraceSpan; trace: TraceDetail }) {
  const tokens = span.tokens
  const result = span.kind === 'tool' && span.output ? parseJson(span.output) : null
  const grafanaUrl = span.metadata?.grafana_url
  return (
    <aside className={styles.detail} data-kind={span.kind} aria-label="Detalle del paso">
      <header className={styles.detailHead}>
        <span className={styles.kind}>{KIND_LABEL[span.kind]}</span>
        <h2>{span.name}</h2>
      </header>

      {span.error && <p className={styles.errorBox}>{span.errorMessage ?? 'El paso terminó con error.'}</p>}

      <dl className={styles.facts}>
        <div><dt>Empezó</dt><dd>a los {formatDuration(span.startMs)}</dd></div>
        <div><dt>Duró</dt><dd>{formatDuration(span.durationMs)}</dd></div>
        <div><dt>Parte del total</dt><dd>{Math.round((span.durationMs / trace.durationMs) * 100)} %</dd></div>
        {tokens && <div><dt>Tokens de entrada</dt><dd>{formatTokens(tokens.prompt)}</dd></div>}
        {tokens && <div><dt>Tokens de salida</dt><dd>{formatTokens(tokens.completion)}</dd></div>}
        {tokens?.cached !== undefined && <div><dt>Leídos de caché</dt><dd>{formatTokens(tokens.cached)}</dd></div>}
      </dl>

      {span.input !== undefined && (
        <section>
          <h3>{span.kind === 'tool' ? 'Argumentos' : span.kind === 'llm' ? 'Mensajes enviados' : 'Recibió'}</h3>
          {isMessages(span.input) ? (
            <Messages messages={span.input} />
          ) : typeof span.input === 'string' ? (
            <p className={styles.text}>{span.input}</p>
          ) : (
            <JsonViewer data={span.input} maxHeight="180px" />
          )}
        </section>
      )}

      {(span.output || span.toolCalls?.length) && (
        <section>
          <h3>{span.kind === 'tool' ? 'Resultado' : 'Respondió'}</h3>
          {span.toolCalls?.length ? <p className={styles.text}>Pidió ejecutar: {span.toolCalls.join(', ')}</p> : null}
          {result ? <JsonViewer data={result} maxHeight="220px" /> : span.output ? <p className={styles.text}>{span.output}</p> : null}
        </section>
      )}

      {span.metadata && (
        <section>
          <h3>Metadata</h3>
          <JsonViewer data={span.metadata} maxHeight="160px" />
        </section>
      )}

      <p className={styles.links}>
        {trace.phoenixUrl && (
          <a href={trace.phoenixUrl} target="_blank" rel="noreferrer">
            Abrir la traza en Phoenix
          </a>
        )}
        {typeof grafanaUrl === 'string' && (
          <a href={grafanaUrl} target="_blank" rel="noreferrer">
            Abrir en Grafana
          </a>
        )}
      </p>
    </aside>
  )
}

// ── Page ────────────────────────────────────────────────────────────

/** The open query and step live in the URL, so a view can be linked (from Phoenix, Grafana or a chat). */
function useLocation(): [{ trace: string | null; step: string | null }, (trace: string, step: string | null) => void] {
  const [location, setLocation] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    return { trace: params.get('trace'), step: params.get('step') }
  })
  const go = useCallback((trace: string, step: string | null) => {
    const params = new URLSearchParams({ trace, ...(step ? { step } : {}) })
    window.history.replaceState(null, '', `?${params}`)
    setLocation({ trace, step })
  }, [])
  return [location, go]
}

type Loaded<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; data: T }

function Login({ rejected, onDone }: { rejected: boolean; onDone: () => void }) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    saveKey(String(new FormData(event.currentTarget).get('key') ?? ''))
    onDone()
  }
  return (
    <form className={styles.login} onSubmit={submit}>
      <label htmlFor="admin-key">Clave de administración</label>
      <input id="admin-key" name="key" type="password" autoComplete="current-password" autoFocus required />
      {rejected && <p className={styles.noteError}>Esa clave no es válida. Prueba otra vez.</p>}
      <button type="submit">Entrar</button>
    </form>
  )
}

export default function TracesApp() {
  const [location, go] = useLocation()
  const [list, setList] = useState<Loaded<TraceSummary[]>>({ state: 'loading' })
  const [detail, setDetail] = useState<Loaded<TraceDetail>>({ state: 'loading' })
  // 'rejected': the stored key was refused, as opposed to never having typed one.
  const [gate, setGate] = useState<'open' | 'ask' | 'rejected'>('open')
  // Bumped by "Actualizar" and after a login, to fetch again.
  const [version, setVersion] = useState(0)

  const failed = useCallback((error: unknown): { state: 'error'; message: string } => {
    if (error instanceof Unauthorized) setGate(hasKey() ? 'rejected' : 'ask')
    return { state: 'error', message: error instanceof Error ? error.message : String(error) }
  }, [])

  useEffect(() => {
    let cancelled = false
    listTraces()
      .then((data) => {
        if (cancelled) return
        setList({ state: 'ready', data })
        // Nothing asked for in the URL: open the latest query.
        if (!new URLSearchParams(window.location.search).get('trace') && data.length) go(data[0].traceId, null)
      })
      .catch((error: unknown) => !cancelled && setList(failed(error)))
    return () => {
      cancelled = true
    }
  }, [version, go, failed])

  const traceId = location.trace
  useEffect(() => {
    if (!traceId) return
    let cancelled = false
    getTrace(traceId)
      .then((data) => !cancelled && setDetail({ state: 'ready', data }))
      .catch((error: unknown) => !cancelled && setDetail(failed(error)))
    return () => {
      cancelled = true
    }
  }, [traceId, version, failed])

  const open = (id: string) => {
    if (id !== traceId) setDetail({ state: 'loading' })
    go(id, null)
  }
  const refresh = () => {
    setList({ state: 'loading' })
    setVersion((v) => v + 1)
  }

  const trace = detail.state === 'ready' && detail.data.traceId === traceId ? detail.data : null
  const flow = useMemo(() => (trace ? buildFlow(trace.spans) : null), [trace])
  const selected = trace ? (trace.spans.find((s) => s.id === location.step) ?? flow?.span ?? trace.spans[0]) : null

  return (
    <div className={styles.page}>
      <header className={styles.top}>
        <h1>Recorridos del agente</h1>
        <a href="/admin">Volver al panel</a>
      </header>

      {gate !== 'open' ? (
        <main className={styles.gate}>
          <Login
            rejected={gate === 'rejected'}
            onDone={() => {
              setGate('open')
              refresh()
            }}
          />
        </main>
      ) : (
        <>
          <nav className={styles.list} aria-label="Consultas recientes">
            <div className={styles.listHead}>
              <h2>Consultas recientes</h2>
              <button type="button" onClick={refresh} disabled={list.state === 'loading'}>
                Actualizar
              </button>
            </div>
            {list.state === 'loading' && <p className={styles.note}>Cargando consultas…</p>}
            {list.state === 'error' && <p className={styles.noteError}>{list.message}</p>}
            {list.state === 'ready' && list.data.length === 0 && (
              <p className={styles.note}>Todavía no hay consultas. Haz una pregunta en el chat y pulsa Actualizar.</p>
            )}
            {list.state === 'ready' && (
              <ul>
                {list.data.map((t) => (
                  <li key={t.traceId}>
                    <button type="button" aria-current={t.traceId === traceId} onClick={() => open(t.traceId)}>
                      <span className={styles.query}>{t.query || 'Sin texto de consulta'}</span>
                      <span className={styles.listMeta}>
                        <span>{clock(t.startedAt)}</span>
                        <span>{formatDuration(t.durationMs)}</span>
                        <span>{formatTokens(t.tokens)} tokens</span>
                      </span>
                      <span className={styles.listMeta}>
                        {t.subAgents > 0 && (
                          <span className={styles.chip}>
                            {t.subAgents} {t.subAgents === 1 ? 'sub-agente' : 'sub-agentes'}
                          </span>
                        )}
                        {t.error && <span className={styles.chipError}>error</span>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </nav>

          <main className={styles.flow}>
            {!traceId && list.state === 'ready' && <p className={styles.note}>Elige una consulta de la lista.</p>}
            {traceId && !trace && detail.state !== 'error' && <p className={styles.note}>Cargando el recorrido…</p>}
            {traceId && detail.state === 'error' && <p className={styles.noteError}>{detail.message}</p>}
            {trace && flow && selected && (
              <>
                {trace.query && <p className={styles.asked}>{trace.query}</p>}
                <FlowItem node={flow} total={trace.durationMs} selected={selected.id} onSelect={(id) => go(trace.traceId, id)} />
              </>
            )}
          </main>

          {trace && selected ? <Detail span={selected} trace={trace} /> : <aside className={styles.detail} />}
        </>
      )}
    </div>
  )
}
