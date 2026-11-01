import { useCallback, useEffect, useRef, useState } from 'react'
import { useSettings } from '../../store/useStore'
import type { SlashPrompt } from '../../types'
import styles from './SlashMenu.module.css'

interface Props {
  filter: string
  onSelect: (template: string) => void
  onClose: () => void
}

/** Remount with `key={filter}` to reset the selection when the filter changes. */
export default function SlashMenu({ filter, onSelect, onClose }: Props) {
  const prompts = useSettings((s) => s.prompts)
  const [selected, setSelected] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered: SlashPrompt[] = filter
    ? prompts.filter(
        (p) =>
          p.name.toLowerCase().includes(filter) ||
          p.desc.toLowerCase().includes(filter) ||
          p.template.toLowerCase().includes(filter),
      )
    : prompts

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      if (!filtered.length) return
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelected((i) => Math.min(i + 1, filtered.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelected((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const item = filtered[selected]
        if (item) onSelect(item.template)
      }
    },
    [filtered, selected, onSelect, onClose],
  )

  useEffect(() => {
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onKeyDown])

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [onClose])

  if (!filtered.length) return null

  return (
    <div ref={containerRef} className={styles.slashMenu} role="listbox" aria-label="Prompts MCP">
      {filtered.map((p, i) => (
        <div key={`${p.group}-${p.name}`}>
          {(i === 0 || filtered[i - 1].group !== p.group) && <div className={styles.groupHeader}>── {p.group} ──</div>}
          <button
            role="option"
            aria-selected={i === selected}
            className={`${styles.slashItem} ${i === selected ? styles.selected : ''}`}
            onMouseEnter={() => setSelected(i)}
            onClick={() => onSelect(p.template)}
          >
            <span className={styles.slashItemName}>
              {p.name}
              {p.isMcp && <span className={styles.mcpBadge}>MCP</span>}
            </span>
            <span className={styles.slashItemDesc}>{p.desc}</span>
          </button>
        </div>
      ))}
    </div>
  )
}
