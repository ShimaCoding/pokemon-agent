import type { DoneEvent } from '../../types'
import styles from './TraceSummary.module.css'

interface Props {
  event: DoneEvent
  toolCallCount: number
}

export default function TraceSummary({ event, toolCallCount }: Props) {
  const tried        = event.models_tried ?? []
  const rotationText = tried.length
    ? ' · ' + tried.map((m) => m.split('/').pop()).join('→')
    : ''

  const totalTokens  = event.total_tokens ?? 0
  const inputTokens  = event.input_tokens ?? 0
  const outputTokens = event.output_tokens ?? 0
  const tokenText    = totalTokens > 0
    ? ` · ${totalTokens.toLocaleString()} tokens (↑${inputTokens.toLocaleString()} ↓${outputTokens.toLocaleString()})`
    : ''

  return (
    <div className={styles.summary}>
      <div className={styles.title}>Resumen de esta consulta</div>
      <div className={styles.vals}>
        Llamadas: {toolCallCount} · Tiempo: {event.elapsed_ms}ms{tokenText}{rotationText}
      </div>
    </div>
  )
}
