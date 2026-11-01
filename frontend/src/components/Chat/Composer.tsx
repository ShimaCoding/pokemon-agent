import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { sendChatMessage } from '../../hooks/useAgentStream'
import { useChat } from '../../store/useStore'
import SlashMenu from './SlashMenu'
import styles from './Composer.module.css'

const MAX_LENGTH = 500

export default function Composer() {
  const inFlight = useChat((s) => s.inFlight)
  const rateLimitSeconds = useChat((s) => s.rateLimitSeconds)
  const [value, setValue] = useState('')
  const [slashFilter, setSlashFilter] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const blocked = inFlight || rateLimitSeconds > 0
  const canSend = value.trim().length > 0 && !blocked

  // Grow with the content up to the CSS max-height.
  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  const submit = useCallback(() => {
    if (!canSend) return
    const query = value
    setValue('')
    setSlashFilter(null)
    void sendChatMessage(query)
  }, [canSend, value])

  function onChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const v = e.target.value
    setValue(v)
    setSlashFilter(v.startsWith('/') ? v.slice(1).toLowerCase() : null)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // The slash menu owns Enter/arrows/Escape while it's open.
    if (slashFilter !== null) return
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const onSlashSelect = useCallback((template: string) => {
    setValue(template)
    setSlashFilter(null)
    inputRef.current?.focus()
  }, [])
  const onSlashClose = useCallback(() => setSlashFilter(null), [])

  return (
    <div className={styles.bottomBar}>
      {rateLimitSeconds > 0 && (
        <div className={styles.rateLimitBanner} role="status">
          ⚡ límite de consultas · espera {rateLimitSeconds}s
        </div>
      )}
      <div className={styles.inner}>
        {slashFilter !== null && (
          <SlashMenu key={slashFilter} filter={slashFilter} onSelect={onSlashSelect} onClose={onSlashClose} />
        )}
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <textarea
            ref={inputRef}
            className={styles.chatInput}
            rows={1}
            value={value}
            onChange={onChange}
            onKeyDown={onKeyDown}
            maxLength={MAX_LENGTH}
            placeholder="Pregunta sobre un Pokémon o escribe / para comandos…"
            aria-label="Mensaje para Dexter"
            autoFocus
          />
          {inFlight && <span className={styles.thinking}>█ pensando…</span>}
          <span
            className={`${styles.statusBadge} ${inFlight ? styles.busy : rateLimitSeconds > 0 ? styles.limited : ''}`}
          >
            {inFlight ? 'procesando' : rateLimitSeconds > 0 ? `espera ${rateLimitSeconds}s` : 'listo'}
          </span>
          <button className={styles.sendButton} type="submit" disabled={!canSend}>
            ENVIAR
          </button>
        </form>
      </div>
    </div>
  )
}
