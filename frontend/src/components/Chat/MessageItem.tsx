import { lazy, memo, Suspense } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AssistantMessage, ChatMessage } from '../../types'
import ThinkingPanel from '../Thinking/ThinkingPanel'
import DexterAvatar from './DexterAvatar'
import styles from './MessageItem.module.css'

// The MCP Apps host (AppBridge + zod) is the bulk of the bundle; load it with the first widget.
const WidgetFrame = lazy(() => import('../Widget/WidgetFrame'))

function AssistantReply({ message }: { message: AssistantMessage }) {
  const streaming = message.status === 'streaming'
  const lastIndex = message.parts.length - 1

  return (
    <article className={styles.assistant} aria-busy={streaming}>
      <DexterAvatar active={streaming} />
      <div className={styles.content}>
        <ThinkingPanel message={message} />
        {message.parts.map((part, i) =>
          part.kind === 'widget' ? (
            <Suspense key={part.event.tool_id} fallback={<div className={styles.widgetPlaceholder} />}>
              <WidgetFrame event={part.event} />
            </Suspense>
          ) : (
            <div key={i} className={`md ${styles.text}`} data-caret={streaming && i === lastIndex}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{part.text}</ReactMarkdown>
            </div>
          ),
        )}
        {message.status === 'error' && (
          <p className={styles.error} role="alert">
            {message.error ?? 'Algo salió mal.'}
          </p>
        )}
      </div>
    </article>
  )
}

function MessageItem({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className={styles.userRow}>
        <p className={styles.user}>{message.text}</p>
      </div>
    )
  }
  return <AssistantReply message={message} />
}

export default memo(MessageItem)
