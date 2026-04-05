import styles from './Timeline.module.css'
import type { TraceEvent } from '../../../types'

const ICONS: Record<string, string> = {
  start: '⚡',
  agent_init: '🚀',
  llm_call: '🧠',
  model_attempt: '🎯',
  tool_call: '🔧',
  tool_result: '📦',
  text: '💬',
  done: '✅',
  error: '❌',
  system_log: 'ℹ',
}

interface Props {
  event: TraceEvent
  onClick?: (eventType: string) => void
}

function previewFor(event: TraceEvent): string {
  switch (event.type) {
    case 'llm_call':
      return `call #${event.call_index}`
    case 'model_attempt':
      return `${event.model.split('/').pop() ?? event.model} · ${event.status}`
    case 'tool_call':
      return `${event.tool}(${Object.keys(event.args).slice(0, 2).join(', ')})`
    case 'tool_result':
      return `idx=${event.index}`
    case 'agent_init':
      return `${event.mcp_tools_count} tools${event.skill_loaded ? ' · ' + event.skill_loaded : ''}`
    case 'done':
      return `${event.elapsed_ms}ms`
    case 'error':
      return event.message ?? 'error'
    case 'system_log':
      return event.message
    case 'text':
      return event.delta.slice(0, 40)
    default:
      return ''
  }
}

export default function TimelineEvent({ event, onClick }: Props) {
  const icon = ICONS[event.type] ?? '·'
  const isClickable = onClick && event.type !== 'system_log' && event.type !== 'text'
  return (
    <div
      className={styles.event}
      onClick={isClickable ? () => onClick(event.type) : undefined}
      role={isClickable ? 'button' : undefined}
    >
      <span className={styles.icon}>{icon}</span>
      <div className={styles.body}>
        <div className={styles.typeLabel}>{event.type}</div>
        <div className={styles.preview}>{previewFor(event)}</div>
      </div>
    </div>
  )
}
