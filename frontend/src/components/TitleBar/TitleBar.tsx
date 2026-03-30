import useStore from '../../store/useStore'
import styles from './TitleBar.module.css'
import McpBadgeDropdown from './McpBadgeDropdown'

export default function TitleBar() {
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const apiKey          = useStore((s) => s.apiKey)
  const mcpTools        = useStore((s) => s.mcpTools)
  const mcpResources    = useStore((s) => s.mcpResources)
  const prompts         = useStore((s) => s.prompts)

  // Filter to MCP-originated prompts for the badge count
  const mcpPrompts = prompts.filter((p) => p.isMcp)

  return (
    <header className={styles.titlebar} id="titlebar">
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
          <button
            className={`${styles.settingsBtn} ${apiKey ? styles.hasKey : ''}`}
            title={apiKey ? 'API Key configurada' : 'Configurar API Key'}
            onClick={() => setSettingsOpen(true)}
          >
            &#9881;
          </button>
        </div>
      </div>
      <div className={styles.bottomRow}>
        <div className={styles.mcpBadges}>
          <McpBadgeDropdown kind="tool"     items={mcpTools}     />
          <McpBadgeDropdown kind="resource" items={mcpResources} />
          <McpBadgeDropdown kind="prompt"   items={mcpPrompts}   />
        </div>
      </div>
    </header>
  )
}
