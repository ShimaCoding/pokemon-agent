import { lazy, memo, Suspense, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { sendFeedback } from '../../lib/api'
import { useChat } from '../../store/useStore'
import type { AssistantMessage, ChatMessage, FeedbackValue } from '../../types'
import ThinkingPanel from '../Thinking/ThinkingPanel'
import styles from './MessageItem.module.css'

// The MCP Apps host (AppBridge + zod) is the bulk of the bundle; load it with the first widget.
const WidgetFrame = lazy(() => import('../Widget/WidgetFrame'))

const THUMBS: { value: FeedbackValue; glyph: string; label: string }[] = [
  { value: 'up', glyph: '👍', label: 'Respuesta útil' },
  { value: 'down', glyph: '👎', label: 'Respuesta no útil' },
]

/** 👍/👎 tied to the reply's Datadog trace; one vote per reply (the backend answers 409 after that). */
function FeedbackButtons({ message, traceId, spanId }: { message: AssistantMessage; traceId: string; spanId: string }) {
  const setFeedback = useChat((s) => s.setFeedback)
  const [failed, setFailed] = useState(false)

  const vote = async (value: FeedbackValue) => {
    setFailed(false)
    setFeedback(message.id, value)
    // 'voted' keeps the choice: the reply was already rated (e.g. from another tab).
    if ((await sendFeedback(traceId, spanId, value)) === 'error') {
      setFeedback(message.id, undefined)
      setFailed(true)
    }
  }

  return (
    <div className={styles.feedback} role="group" aria-label="Valorar respuesta">
      {THUMBS.map(({ value, glyph, label }) => (
        <button
          key={value}
          type="button"
          className={styles.thumb}
          aria-label={label}
          title={label}
          aria-pressed={message.feedback === value}
          disabled={message.feedback !== undefined}
          onClick={() => vote(value)}
        >
          {glyph}
        </button>
      ))}
      {failed && <span className={styles.feedbackError}>No se pudo enviar</span>}
    </div>
  )
}

function AssistantReply({ message }: { message: AssistantMessage }) {
  const streaming = message.status === 'streaming'
  const lastIndex = message.parts.length - 1

  return (
    <article className={styles.assistant} aria-busy={streaming}>
      <header className={styles.speaker}>
        <img src="/favicon.svg" alt="" className={styles.logo} />
        DEXTER
      </header>
      <div className={styles.content}>
        <ThinkingPanel message={message} />
        {message.parts.map((part, i) =>
          part.kind === 'widget' ? (
            <Suspense key={part.event.tool_id} fallback={<div className={styles.widgetPlaceholder}>CARGANDO WIDGET...</div>}>
              <WidgetFrame event={part.event} />
            </Suspense>
          ) : (
            <div key={i} className={`md-content ${styles.text}`} data-caret={streaming && i === lastIndex}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{part.text}</ReactMarkdown>
            </div>
          ),
        )}
        {message.status === 'error' && (
          <p className={styles.error} role="alert">
            ✗ {message.error ?? 'Algo salió mal.'}
          </p>
        )}
        {message.status === 'done' && message.done?.trace_id && message.done.span_id && (
          <FeedbackButtons message={message} traceId={message.done.trace_id} spanId={message.done.span_id} />
        )}
      </div>
    </article>
  )
}

function MessageItem({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className={styles.userRow}>
        <div className={styles.user}>
          <span className={styles.userLabel}>TÚ</span>
          <p className={styles.userText}>{message.text}</p>
        </div>
      </div>
    )
  }
  return <AssistantReply message={message} />
}

export default memo(MessageItem)
