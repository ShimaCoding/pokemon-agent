import useStore from '../../store/useStore'
import styles from './AdvancedPanel.module.css'

export default function AdvancedPanel() {
  const pokemonData = useStore((s) => s.pokemonData)

  return (
    <div className={styles.advancedPanel} id="advanced-panel">
      {/* LEFT: Pokémon data + leyenda + ¿Qué es MCP? */}
      <div className={styles.advLeft} id="adv-left">
        <div className={styles.advSection} id="adv-data-section">
          <div className={styles.advSectionTitle}>
            <span>GBC Pokémon Data</span>
          </div>
          <div className={styles.advSectionContent} id="adv-pokemon-info">
            <div className={styles.advDataRow}>
              <span className={styles.advDataKey}>Pokémon · </span>
              <span id="adv-name">{pokemonData?.name ?? '--'}</span>
            </div>
            <div className={styles.advDataRow}>
              <span className={styles.advDataKey}>Tipos · </span>
              <span id="adv-types">
                {pokemonData ? pokemonData.types.join(', ') : '--'}
              </span>
            </div>
            <div className={styles.advDataRow}>
              <span className={styles.advDataKey}>Altura · </span>
              <span id="adv-height">{pokemonData?.height ?? '--'}</span>
            </div>
            <div className={styles.advDataRow}>
              <span className={styles.advDataKey}>Peso · </span>
              <span id="adv-weight">{pokemonData?.weight ?? '--'}</span>
            </div>
            <div className={styles.advDataRow}>
              <span className={styles.advDataKey}>Nº Dex · </span>
              <span id="adv-number">{pokemonData?.number ?? '--'}</span>
            </div>
          </div>
        </div>

        <div className={styles.advSection}>
          <div className={styles.advSectionTitle}>LEYENDA MCP</div>
          <div className={styles.advSectionContent}>
            <div className={styles.legendItem}>
              <div className={`${styles.legendDot} ${styles.tool}`} />
              <span>· Tool call</span>
            </div>
            <div className={styles.legendItem}>
              <div className={`${styles.legendDot} ${styles.resource}`} />
              <span>· Resource</span>
            </div>
            <div className={styles.legendItem}>
              <div className={`${styles.legendDot} ${styles.prompt}`} />
              <span>· Prompt template</span>
            </div>
          </div>
        </div>

        <div className={`${styles.advSection} ${styles.flexGrow}`}>
          <div className={styles.advSectionTitle}>¿Qué es MCP?</div>
          <div className={styles.advSectionContentWide}>
            MCP (Model Context Protocol) conecta LLMs con herramientas externas.
            Cada consulta puede usar Tools, Resources y Prompts del servidor
            mcpokedex.com para traer datos en tiempo real.
          </div>
        </div>
      </div>

      {/* RIGHT: redirect to Dexter console */}
      <div className={styles.advRight} id="adv-right">
        <div className={styles.redirectMsg}>
          los logs están en la<br />
          ▼ Consola de Dexter<br />
          (panel inferior)
        </div>
      </div>
    </div>
  )
}
