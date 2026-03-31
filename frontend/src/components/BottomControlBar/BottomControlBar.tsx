import { useState, useRef, useCallback, useEffect } from 'react'
import useStore from '../../store/useStore'
import { useAgentStream } from '../../hooks/useAgentStream'
import SlashMenu from './SlashMenu'
import ProviderSelect from './ProviderSelect'
import ChatInput from './ChatInput'
import SendButton from './SendButton'
import styles from './BottomControlBar.module.css'

export default function BottomControlBar() {
  const inFlight          = useStore((s) => s.inFlight)
  const rateLimitSeconds  = useStore((s) => s.rateLimitSeconds)
  const queryDraft        = useStore((s) => s.queryDraft)
  const clearQueryDraft   = useStore((s) => s.clearQueryDraft)

  const [inputValue, setInputValue] = useState('')
  const [slashOpen, setSlashOpen] = useState(false)
  const [slashFilter, setSlashFilter] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const { runQuery } = useAgentStream()

  // Pre-fill input when an example is selected from the intro modal
  useEffect(() => {
    if (queryDraft) {
      setInputValue(queryDraft)
      clearQueryDraft()
      inputRef.current?.focus()
    }
  }, [queryDraft, clearQueryDraft])

  const handleSubmit = useCallback(async () => {
    const query = inputValue.trim()
    // SECURITY (Finding 2): enforce client-side length cap to prevent oversized
    // payloads and reduce prompt-injection surface area.
    if (!query || query.length > 500 || inFlight || rateLimitSeconds > 0) return
    setInputValue('')
    setSlashOpen(false)
    await runQuery(query)
  }, [inputValue, inFlight, rateLimitSeconds, runQuery])

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
      {rateLimitSeconds > 0 && (
        <div className={styles.rateLimitBanner} id="rate-limit-banner">
          ⚡ espera {rateLimitSeconds}s
        </div>
      )}
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
        className={`${styles.statusBadge} ${inFlight ? styles.busy : rateLimitSeconds > 0 ? styles.limited : ''}`}
        id="status-badge"
      >
        {inFlight ? 'procesando' : rateLimitSeconds > 0 ? `espera ${rateLimitSeconds}s` : 'listo'}
      </span>
      <SendButton onClick={handleSubmit} />
    </div>
  )
}
