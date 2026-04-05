import useStore from '../../store/useStore'
import styles from './TitleBar.module.css'
import McpBadgeDropdown from './McpBadgeDropdown'

export default function TitleBar() {
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const setIntroOpen    = useStore((s) => s.setIntroOpen)
  const apiKey          = useStore((s) => s.apiKey)
  const mcpTools        = useStore((s) => s.mcpTools)
  const mcpResources    = useStore((s) => s.mcpResources)
  const prompts         = useStore((s) => s.prompts)
  const uiMode          = useStore((s) => s.uiMode)
  const setUiMode       = useStore((s) => s.setUiMode)
  const fastForward     = useStore((s) => s.fastForward)
  const setFastForward  = useStore((s) => s.setFastForward)

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
          <div className={styles.modeSegmented} role="tablist" aria-label="UI mode">
            <button
              type="button"
              className={`${styles.modeOption} ${uiMode === 'casual' ? styles.modeActive : ''}`}
              title="Modo CASUAL: Pokédex tradicional"
              onClick={() => setUiMode('casual')}
              role="tab"
              aria-selected={uiMode === 'casual'}
            >
              POKé
            </button>
            <button
              type="button"
              className={`${styles.modeOption} ${uiMode === 'dev' ? styles.modeActive : ''}`}
              title="Modo DEV: consola con traza MCP"
              onClick={() => setUiMode('dev')}
              role="tab"
              aria-selected={uiMode === 'dev'}
            >
              DEV
            </button>
            <button
              type="button"
              className={`${styles.modeOption} ${uiMode === 'learn' ? styles.modeActive : ''}`}
              title="Modo LEARN: wiki viviente del agente"
              onClick={() => setUiMode('learn')}
              role="tab"
              aria-selected={uiMode === 'learn'}
            >
              LEARN
            </button>
          </div>
          <button
            className={`${styles.fastForwardBtn} ${fastForward ? styles.fastActive : ''}`}
            title={fastForward ? 'Velocidad ultra-rápida (Sin animaciones)' : 'Velocidad de lectura (Con animaciones)'}
            onClick={() => setFastForward(!fastForward)}
          >
            &gt;&gt;
          </button>
          <button
            className={styles.helpBtn}
            title="¿Cómo usar?"
            onClick={() => setIntroOpen(true)}
          >
            ?
          </button>
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
