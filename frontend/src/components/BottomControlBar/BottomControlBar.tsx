import { useState, useRef, useCallback } from 'react'
import useStore from '../../store/useStore'
import { useAgentStream } from '../../hooks/useAgentStream'
import SlashMenu from './SlashMenu'
import ProviderSelect from './ProviderSelect'
import ChatInput from './ChatInput'
import SendButton from './SendButton'
import styles from './BottomControlBar.module.css'

export default function BottomControlBar() {
  const inFlight = useStore((s) => s.inFlight)
  const [inputValue, setInputValue] = useState('')
  const [slashOpen, setSlashOpen] = useState(false)
  const [slashFilter, setSlashFilter] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const { runQuery } = useAgentStream()

  const handleSubmit = useCallback(async () => {
    const query = inputValue.trim()
    if (!query || inFlight) return
    setInputValue('')
    setSlashOpen(false)
    await runQuery(query)
  }, [inputValue, inFlight, runQuery])

  const handleSlashSelect = useCallback((template: string) => {
    setInputValue(template)
    setSlashOpen(false)
    inputRef.current?.focus()
  }, [])

  const handleSlashOpen = useCallback((filter: string) => {
    setSlashFilter(filter)
    setSlashOpen(true)
  }, [])

  const handleSlashClose = useCallback(() => {
    setSlashOpen(false)
    setSlashFilter('')
  }, [])

  return (
    <div className={styles.bottomBar} id="bottom-bar">
      {slashOpen && (
        <SlashMenu
          filter={slashFilter}
          onSelect={handleSlashSelect}
          onClose={handleSlashClose}
        />
      )}
      <ProviderSelect />
      <ChatInput
        ref={inputRef}
        value={inputValue}
        onChange={setInputValue}
        onSubmit={handleSubmit}
        onSlashOpen={handleSlashOpen}
        onSlashClose={handleSlashClose}
        slashMenuOpen={slashOpen}
      />
      {inFlight && (
        <span className={styles.thinking} id="thinking">
          █ pensando…
        </span>
      )}
      <span
        className={`${styles.statusBadge} ${inFlight ? styles.busy : ''}`}
        id="status-badge"
      >
        {inFlight ? 'procesando' : 'listo'}
      </span>
      <SendButton onClick={handleSubmit} />
    </div>
  )
}
