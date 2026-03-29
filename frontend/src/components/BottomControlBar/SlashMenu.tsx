import { useState, useRef, useCallback, useEffect } from 'react'
import useStore from '../../store/useStore'
import type { SlashPrompt } from '../../types'
import styles from './SlashMenu.module.css'

interface Props {
  onSelect: (template: string) => void
  onClose: () => void
  filter: string
}

export default function SlashMenu({ onSelect, onClose, filter }: Props) {
  const prompts = useStore((s) => s.prompts)
  const [selectedIdx, setSelectedIdx] = useState(0)

  const filtered: SlashPrompt[] = filter
    ? prompts.filter(
        (p) =>
          p.name.toLowerCase().includes(filter) ||
          p.desc.toLowerCase().includes(filter) ||
          p.template.toLowerCase().includes(filter)
      )
    : prompts

  // Reset selection when filter changes
  useEffect(() => { setSelectedIdx(0) }, [filter])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!filtered.length) return
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIdx((i) => Math.min(i + 1, filtered.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIdx((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const item = filtered[selectedIdx]
        if (item) onSelect(item.template)
      } else if (e.key === 'Escape') {
        onClose()
      }
    },
    [filtered, selectedIdx, onSelect, onClose]
  )

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  const containerRef = useRef<HTMLDivElement>(null)

  // Close when clicking outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [onClose])

  if (!filtered.length) return null

  let currentGroup = ''

  return (
    <div ref={containerRef} className={styles.slashMenu} id="slash-menu">
      {filtered.map((p, i) => {
        const isNewGroup = p.group !== currentGroup
        if (isNewGroup) currentGroup = p.group
        return (
          <div key={i}>
            {isNewGroup && (
              <div className={styles.groupHeader}>── {p.group} ──</div>
            )}
            <div
              className={`${styles.slashItem} ${i === selectedIdx ? styles.selected : ''}`}
              onMouseEnter={() => setSelectedIdx(i)}
              onClick={() => onSelect(p.template)}
            >
              <span className={styles.slashItemName}>
                {p.name}
                {p.isMcp && <span className={styles.mcpBadge}> MCP</span>}
              </span>
              <span className={styles.slashItemDesc}>{p.desc}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
