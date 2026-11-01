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
    <div className={styles.wrap}>
      {slashFilter !== null && <SlashMenu key={slashFilter} filter={slashFilter} onSelect={onSlashSelect} onClose={onSlashClose} />}
      <form
        className={styles.box}
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <textarea
          ref={inputRef}
          className={styles.input}
          rows={1}
          value={value}
          onChange={onChange}
          onKeyDown={onKeyDown}
          maxLength={MAX_LENGTH}
          placeholder="Pregúntale a Dexter, o escribe / para ver prompts MCP"
          aria-label="Mensaje para Dexter"
          autoFocus
        />
        <button className={styles.send} type="submit" disabled={!canSend} aria-label="Enviar">
          {inFlight ? (
            <span className={styles.stop} aria-hidden="true" />
          ) : (
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
              <path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </button>
      </form>
      <p className={styles.note} aria-live="polite">
        {rateLimitSeconds > 0
          ? `Límite de consultas alcanzado. Podrás preguntar de nuevo en ${rateLimitSeconds} s.`
          : value.length > MAX_LENGTH - 80
            ? `${value.length}/${MAX_LENGTH} caracteres`
            : 'Dexter usa modelos gpt-oss vía Groq y herramientas MCP. Puede equivocarse; no lo admitirá.'}
      </p>
    </div>
  )
}
