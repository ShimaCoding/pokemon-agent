import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import type { McpPrompt, McpResource, McpTool } from '../../types'
import styles from './McpBadgeDropdown.module.css'

type BadgeKind = 'tool' | 'resource' | 'prompt'

interface Props {
  kind: BadgeKind
  items: McpTool[] | McpResource[] | McpPrompt[]
  loading?: boolean
}

function getLabel(kind: BadgeKind): string {
  return { tool: 'TOOL', resource: 'RESOURCE', prompt: 'PROMPT' }[kind]
}

function getHeaderLabel(kind: BadgeKind, count: number): string {
  const labels: Record<BadgeKind, string> = {
    tool:     'HERRAMIENTAS MCP',
    resource: 'RECURSOS MCP',
    prompt:   'PROMPTS MCP',
  }
  return `${labels[kind]} (${count})`
}

function renderItem(kind: BadgeKind, item: McpTool | McpResource | McpPrompt, idx: number) {
  if (kind === 'tool') {
    const t = item as McpTool
    return (
      <div key={idx} className={styles.item}>
        <div className={styles.itemName}>{t.name}</div>
        {t.description && <div className={styles.itemDesc}>{t.description}</div>}
      </div>
    )
  }
  if (kind === 'resource') {
    const r = item as McpResource
    return (
      <div key={idx} className={styles.item}>
        <div className={styles.itemName}>{r.name || r.uri}</div>
        {r.description && <div className={styles.itemDesc}>{r.description}</div>}
        {r.uri && <div className={styles.itemDesc} style={{ opacity: 0.5 }}>{r.uri}</div>}
      </div>
    )
  }
  // prompt
  const p = item as McpPrompt
  return (
    <div key={idx} className={styles.item}>
      <div className={styles.itemName}>{p.name}</div>
      {p.description && <div className={styles.itemDesc}>{p.description}</div>}
    </div>
  )
}

export default function McpBadgeDropdown({ kind, items, loading }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const [dropPos, setDropPos] = useState<{ top: number; right: number } | null>(null)

  // Compute fixed position when opening
  useEffect(() => {
    if (open && ref.current) {
      const rect = ref.current.getBoundingClientRect()
      setDropPos({
        top: rect.bottom + 4,
        right: window.innerWidth - rect.right,
      })
    }
  }, [open])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  const count = items.length

  const dropdown = open && dropPos ? (
    <div
      className={styles.dropdown}
      style={{ position: 'fixed', top: dropPos.top, right: dropPos.right }}
    >
      <div className={`${styles.header} ${styles[kind]}`}>
        {loading ? '...' : getHeaderLabel(kind, count)}
      </div>
      {loading ? (
        <div className={styles.loading}>CARGANDO...</div>
      ) : count === 0 ? (
        <div className={styles.empty}>Sin datos</div>
      ) : (
        items.map((item, idx) => renderItem(kind, item, idx))
      )}
    </div>
  ) : null

  return (
    <div className={styles.wrapper} ref={ref}>
      <button
        className={`${styles.badge} ${styles[kind]} ${open ? styles.active : ''}`}
        onClick={() => setOpen((v) => !v)}
        title={`Ver ${getLabel(kind)}s del servidor MCP`}
      >
        {getLabel(kind)}
        {!loading && count > 0 && (
          <span className={styles.count}>({count})</span>
        )}
      </button>
      {dropdown && createPortal(dropdown, document.body)}
    </div>
  )
}
