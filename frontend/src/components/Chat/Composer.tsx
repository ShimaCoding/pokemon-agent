import { sendChatMessage } from '../../hooks/useAgentStream'
import { useMessageInput } from './useMessageInput'
import styles from './Composer.module.css'

function send(query: string) {
  void sendChatMessage(query)
}

export default function Composer() {
  const { inFlight, rateLimitSeconds, canSend, submit, slashMenu, inputProps } = useMessageInput({ onSend: send })

  return (
    <div className={styles.bottomBar}>
      {rateLimitSeconds > 0 && (
        <div className={styles.rateLimitBanner} role="status">
          ⚡ límite de consultas · espera {rateLimitSeconds}s
        </div>
      )}
      <div className={styles.inner}>
        {slashMenu}
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <textarea
            {...inputProps}
            className={styles.chatInput}
            placeholder="Pregunta sobre un Pokémon o escribe / para comandos…"
            aria-label="Mensaje para Dexter"
            autoFocus
          />
          {inFlight && <span className={styles.thinking}>█ pensando…</span>}
          <button className={styles.sendButton} type="submit" disabled={!canSend}>
            ENVIAR
          </button>
        </form>
      </div>
    </div>
  )
}
