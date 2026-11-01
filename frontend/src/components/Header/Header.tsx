import { useChat, useSettings } from '../../store/useStore'
import McpPopover from './McpPopover'
import styles from './Header.module.css'

export default function Header() {
  const hasMessages = useChat((s) => s.messages.length > 0)
  const inFlight = useChat((s) => s.inFlight)
  const newConversation = useChat((s) => s.newConversation)
  const apiKey = useSettings((s) => s.apiKey)
  const setSettingsOpen = useSettings((s) => s.setSettingsOpen)

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <span className={styles.logo}>MCPokédex</span>
      </div>
      <div className={styles.actions}>
        <McpPopover />
        <button
          className={styles.iconBtn}
          onClick={newConversation}
          disabled={!hasMessages || inFlight}
          title="Nueva conversación"
          aria-label="Nueva conversación"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <span className={styles.btnLabel}>Nueva</span>
        </button>
        <button
          className={styles.iconBtn}
          onClick={() => setSettingsOpen(true)}
          title={apiKey ? 'API key configurada' : 'Ajustes'}
          aria-label="Ajustes"
          data-active={Boolean(apiKey)}
        >
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path
              d="M15 7a4 4 0 1 1-3.87 5H9v2H7v-2H5v-2h6.13A4 4 0 0 1 15 7Zm0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z"
              fill="currentColor"
            />
          </svg>
        </button>
      </div>
    </header>
  )
}
