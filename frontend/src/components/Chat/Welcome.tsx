import { useState } from 'react'
import { flushSync } from 'react-dom'
import { sendChatMessage } from '../../hooks/useAgentStream'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { randomSuggestions } from './suggestions'
import { useMessageInput } from './useMessageInput'
import styles from './Welcome.module.css'

/**
 * Send the first message and let the dialog box fly down into the bottom bar:
 * both share `view-transition-name: dexter-input`, and the 8-bit `steps()`
 * timing lives in global.css.
 */
function sendWithTransition(query: string) {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (typeof document.startViewTransition !== 'function' || reduceMotion) {
    void sendChatMessage(query)
    return
  }
  // The new snapshot must already show the chat, so render synchronously.
  document.startViewTransition(() => flushSync(() => void sendChatMessage(query)))
}

export default function Welcome() {
  const [hover, setHover] = useState(0)
  // Picked once per mount, so every page load shows different examples.
  const [suggestions] = useState(randomSuggestions)
  const { inFlight, rateLimitSeconds, canSend, submit, slashMenu, inputRef, inputProps } = useMessageInput({
    onSend: sendWithTransition,
    keepValueOnSend: true,
    slashPlacement: 'below',
  })
  // On touch screens the keyboard should only open when the user taps the box.
  const touch = useMediaQuery('(pointer: coarse)')

  return (
    <section className={styles.welcome}>
      {/* The whole box is the input: a click anywhere puts the cursor in it. */}
      <div className={styles.dialog} onClick={() => inputRef.current?.focus()}>
        {slashMenu}
        <p className={styles.line}>¡Hola, entrenador! Soy DEXTER, la Pokédex del Profesor Oak.</p>
        <p className={styles.line}>
          Pregúntame lo que quieras. Consultaré el servidor MCP y juzgaré tu pregunta con rigor científico.
        </p>
        <form
          className={styles.inputRow}
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <span className={styles.prompt} aria-hidden="true">▶</span>
          <textarea
            {...inputProps}
            className={styles.input}
            placeholder="Escribe aquí tu pregunta…"
            aria-label="Mensaje para Dexter"
            autoFocus={!touch}
          />
          <button className={styles.more} type="submit" disabled={!canSend} aria-label="Enviar">
            ▼
          </button>
        </form>
        {rateLimitSeconds > 0 ? (
          <p className={styles.status} role="status">⚡ Límite de consultas · espera {rateLimitSeconds}s</p>
        ) : (
          inFlight && <p className={styles.status}>█ pensando…</p>
        )}
      </div>
      <nav className={styles.menu} aria-label="Ejemplos">
        {suggestions.map((s, i) => (
          <button
            key={s.title}
            className={styles.option}
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onClick={() => sendWithTransition(s.prompt)}
          >
            <span className={styles.cursor}>{hover === i ? '▶' : ''}</span>
            <span className={styles.optionTitle}>{s.title}</span>
            <span className={styles.optionPrompt}>{s.prompt}</span>
          </button>
        ))}
      </nav>
      <p className={styles.hint}>Escribe / en el cuadro para usar los prompts del servidor MCP.</p>
    </section>
  )
}
