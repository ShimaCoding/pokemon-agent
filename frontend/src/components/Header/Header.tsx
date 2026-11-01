import { useChat, useSettings } from '../../store/useStore'
import McpBadgeDropdown from './McpBadgeDropdown'
import styles from './Header.module.css'

export default function Header() {
  const hasMessages = useChat((s) => s.messages.length > 0)
  const inFlight = useChat((s) => s.inFlight)
  const newConversation = useChat((s) => s.newConversation)
  const apiKey = useSettings((s) => s.apiKey)
  const setSettingsOpen = useSettings((s) => s.setSettingsOpen)
  const mcpTools = useSettings((s) => s.mcpTools)
  const mcpResources = useSettings((s) => s.mcpResources)
  const prompts = useSettings((s) => s.prompts)
  const loaded = useSettings((s) => s.mcpLoaded)

  const mcpPrompts = prompts
    .filter((p) => p.isMcp)
    .map((p) => ({ name: p.name, description: p.desc }))

  return (
    <header className={styles.titlebar}>
      <div className={styles.topRow}>
        <div className={styles.titleMain}>
          <img src="/favicon.svg" alt="" className={styles.titleLogo} />
          <span className={styles.mainTitle}>MCPokédex</span>
        </div>
        <div className={styles.githubLinks}>
          <img src="/github.svg" alt="GitHub" className={styles.githubIcon} />
          <a
            className={styles.githubLink}
            href="https://github.com/ShimaCoding/mcp-pokemon-server"
            target="_blank"
            rel="noopener noreferrer"
          >
            mcp-pokemon-server
          </a>
          <span className={styles.githubSep}>·</span>
          <a
            className={styles.githubLink}
            href="https://github.com/ShimaCoding/pokemon-agent"
            target="_blank"
            rel="noopener noreferrer"
          >
            pokemon-agent
          </a>
        </div>
        <div className={styles.actions}>
          <button
            className={styles.newBtn}
            onClick={newConversation}
            disabled={!hasMessages || inFlight}
            title="Empezar una conversación nueva (Dexter olvida la anterior)"
          >
            + NUEVA
          </button>
          <button
            className={`${styles.settingsBtn} ${apiKey ? styles.hasKey : ''}`}
            onClick={() => setSettingsOpen(true)}
            title={apiKey ? 'API key configurada' : 'Ajustes'}
            aria-label="Ajustes"
          >
            ⚙
          </button>
        </div>
      </div>
      <div className={styles.bottomRow}>
        <div className={styles.mcpBadges}>
          <McpBadgeDropdown kind="tool" items={mcpTools} loading={!loaded} />
          <McpBadgeDropdown kind="resource" items={mcpResources} loading={!loaded} />
          <McpBadgeDropdown kind="prompt" items={mcpPrompts} loading={!loaded} />
        </div>
      </div>
    </header>
  )
}
