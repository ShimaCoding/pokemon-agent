import useStore from '../../store/useStore'
import styles from './TitleBar.module.css'

export default function TitleBar() {
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const apiKey          = useStore((s) => s.apiKey)

  return (
    <header className={styles.titlebar} id="titlebar">
      <div className={styles.titleLeft}>
        <span className={styles.mainTitle}>MCPokédex GBC</span>
        <span className={styles.subtitle}>Press Start 2P</span>
      </div>
      <div className={styles.titleRight}>
        <div className={styles.mcpBadges}>
          <span className={`${styles.mcpBadge} ${styles.tool}`}>TOOL</span>
          <span className={`${styles.mcpBadge} ${styles.resource}`}>RESOURCE</span>
          <span className={`${styles.mcpBadge} ${styles.prompt}`}>PROMPT</span>
        </div>
        <button
          className={`${styles.settingsBtn} ${apiKey ? styles.hasKey : ''}`}
          title={apiKey ? 'API Key configurada' : 'Configurar API Key'}
          onClick={() => setSettingsOpen(true)}
        >
          &#9881;
        </button>
      </div>
    </header>
  )
}
