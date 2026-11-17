import { sendChatMessage } from '../../hooks/useAgentStream'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useChat } from '../../store/useStore'
import { useMessageInput } from './useMessageInput'
import styles from './Composer.module.css'

function send(query: string) {
  void sendChatMessage(query)
}

export default function Composer() {
  const { inFlight, rateLimitSeconds, canSend, submit, slashMenu, inputProps } = useMessageInput({ onSend: send })
  const compact = useMediaQuery('(max-width: 640px)')
  // On touch screens focusing the field pops the keyboard over the answer.
  const touch = useMediaQuery('(pointer: coarse)')
  const newConversation = useChat((s) => s.newConversation)

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
          <button
            type="button"
            className={styles.newButton}
            onClick={newConversation}
            disabled={inFlight}
            title="Conversación nueva (Dexter olvida esta)"
            aria-label="Conversación nueva"
          >
            ✚
          </button>
          <textarea
            {...inputProps}
            className={styles.chatInput}
            placeholder={compact ? 'Pregunta o escribe /…' : 'Pregunta sobre un Pokémon o escribe / para comandos…'}
            aria-label="Mensaje para Dexter"
            autoFocus={!touch}
          />
          {inFlight && <span className={styles.thinking}>█ pensando…</span>}
          <button className={styles.sendButton} type="submit" disabled={!canSend} aria-label="Enviar">
            {compact ? '▶' : 'ENVIAR'}
          </button>
        </form>
      </div>
    </div>
  )
}
