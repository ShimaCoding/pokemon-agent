import { useEffect } from 'react'
import useStore from '../../store/useStore'
import styles from './IntroModal.module.css'

const EXAMPLES = [
  'Dame la entrada de pokedex de pikachu',
  'Usa el prompt battle/matchup-analysis con pokemon1=pikachu pokemon2=charizard scenario=learning environment=neutral',
  'Dime los stats de Mewtwo',
  '/narrator eevee',
  '¿Cuál es el Pokémon más poderoso?',
]

export default function IntroModal() {
  const introOpen        = useStore((s) => s.introOpen)
  const setIntroOpen     = useStore((s) => s.setIntroOpen)
  const introDismissed   = useStore((s) => s.introDismissed)
  const setIntroDismissed = useStore((s) => s.setIntroDismissed)
  const setQueryDraft    = useStore((s) => s.setQueryDraft)

  const setSettingsDismissed = useStore((s) => s.setSettingsDismissed)
  const setSettingsOpen      = useStore((s) => s.setSettingsOpen)

  const mcpTools     = useStore((s) => s.mcpTools)
  const mcpResources = useStore((s) => s.mcpResources)
  const mcpLoaded    = useStore((s) => s.mcpLoaded)
  const prompts      = useStore((s) => s.prompts)
  const mcpPrompts   = prompts.filter((p) => p.isMcp)

  // Auto-open on first visit
  useEffect(() => {
    if (!introDismissed) {
      setIntroOpen(true)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleClose() {
    setIntroDismissed(true)
    setIntroOpen(false)
  }

  function handleExample(text: string) {
    setQueryDraft(text, true)
    setSettingsDismissed(true)
    setSettingsOpen(false)
    handleClose()
  }

  if (!introOpen) return null

  return (
    <div className={styles.modal}>
      <div className={styles.overlay} onClick={handleClose} />
      <div className={styles.box}>

        {/* Header */}
        <div className={styles.header}>
          <div className={styles.headerLeft}>
            <span className={styles.dot}>◉</span>
            <div>
              <div className={styles.title}>MCPokédex — SISTEMA AGENTICO</div>
              <div className={styles.subtitle}>Tu Pokédex de inteligencia artificial</div>
            </div>
          </div>
          <button className={styles.closeBtn} onClick={handleClose} title="Cerrar">×</button>
        </div>

        <div className={styles.scrollArea}>

          {/* How to use */}
          <div className={styles.section}>
            <div className={styles.sectionTitle}>¿CÓMO USAR?</div>
            <ol className={styles.steps}>
              <li>
                <span className={styles.stepLabel}>Escribe el nombre o número de un Pokémon</span>
                <span className={styles.stepHint}>Ej: "pikachu", "25", "charizard"</span>
              </li>
              <li>
                <span className={styles.stepLabel}>El agente Dexter consulta sus herramientas automáticamente</span>
                <span className={styles.stepHint}>Sin que tengas que pedirlo — flujo 100% agentico</span>
              </li>
              <li>
                <span className={styles.stepLabel}>Sigue el flujo en la pestaña CONSOLA</span>
                <span className={styles.stepHint}>Cada herramienta usada aparece en tiempo real</span>
              </li>
              <li>
                <span className={styles.stepLabel}>Lee la respuesta de Dexter en la pestaña DEXTER</span>
                <span className={styles.stepHint}>Narración con la personalidad sarcástica de Dexter</span>
              </li>
            </ol>
          </div>

          {/* Examples */}
          <div className={styles.section}>
            <div className={styles.sectionTitle}>PRUEBA ESTOS EJEMPLOS</div>
            <div className={styles.exampleList}>
              {EXAMPLES.map((ex) => (
                <button key={ex} className={styles.exampleBtn} onClick={() => handleExample(ex)}>
                  <span className={styles.exampleArrow}>&gt;</span>
                  {ex}
                </button>
              ))}
            </div>
          </div>

          {/* Mode toggle */}
          <div className={styles.section}>
            <div className={styles.sectionTitle}>MODOS DE VISTA</div>
            <div className={styles.toolList}>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgeLocal}`}>DEV</span>
                <span className={styles.toolName}>Modo consola — muestra el trace de herramientas en tiempo real <strong>(por defecto)</strong></span>
              </div>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgeTool}`}>POKé</span>
                <span className={styles.toolName}>Modo Pokédex — muestra la ficha visual del Pokémon y la respuesta de Dexter</span>
              </div>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgePrompt}`}>&gt;&gt;</span>
                <span className={styles.toolName}>Fast Forward — salta las animaciones de escritura, muestra la respuesta al instante</span>
              </div>
            </div>
            <div className={styles.toolsHint}>
              Cambia el modo con DEV / POKé · activa Fast Forward con el botón &gt;&gt; en la barra superior
            </div>
          </div>

          {/* Provider & Fallback */}
          <div className={styles.section}>
            <div className={styles.sectionTitle}>PROVEEDOR DE IA Y FALLBACK</div>
            <div className={styles.toolList}>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgeResource}`}>GRATIS</span>
                <span className={styles.toolName}>OpenRouter — modelos gratuitos, <strong>sin API key</strong></span>
              </div>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgeLocal}`}>KEY</span>
                <span className={styles.toolName}>Groq — modelos ultra-rápidos</span>
              </div>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgeLocal}`}>KEY</span>
                <span className={styles.toolName}>Gemini — gemini-2.5-flash</span>
              </div>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgeLocal}`}>KEY</span>
                <span className={styles.toolName}>OpenAI — gpt-4o-mini</span>
              </div>
              <div className={styles.toolItem}>
                <span className={`${styles.badge} ${styles.badgeSoon}`}>PRONTO</span>
                <span className={styles.toolName}>Ollama — modelos locales sin internet</span>
              </div>
            </div>
            <div className={styles.toolsHint}>
              Cambia el proveedor desde el selector en la barra inferior ↓
            </div>
            <div className={styles.fallbackHint}>
              ⚡ Fallback automático — si el proveedor activo falla, el sistema intenta el siguiente sin interrupciones
            </div>
          </div>

          {/* Tools */}
          <div className={styles.section}>
            <div className={styles.sectionTitle}>HERRAMIENTAS DISPONIBLES</div>
            <div className={styles.toolList}>
              {!mcpLoaded && (
                <div className={styles.toolItem}>
                  <span className={`${styles.badge} ${styles.badgeLocal}`}>MCP</span>
                  <span className={`${styles.toolName} ${styles.loading}`}>Conectando al servidor MCP…</span>
                </div>
              )}
              {mcpTools.map((t) => (
                <div key={t.name} className={styles.toolItem}>
                  <span className={`${styles.badge} ${styles.badgeTool}`}>TOOL</span>
                  <span className={styles.toolName}>{t.name}</span>
                </div>
              ))}
              {mcpResources.map((r) => (
                <div key={r.uri} className={styles.toolItem}>
                  <span className={`${styles.badge} ${styles.badgeResource}`}>REC</span>
                  <span className={styles.toolName}>{r.name}</span>
                </div>
              ))}
              {mcpPrompts.map((p) => (
                <div key={p.name} className={styles.toolItem}>
                  <span className={`${styles.badge} ${styles.badgePrompt}`}>PROMPT</span>
                  <span className={styles.toolName}>/{p.name}</span>
                </div>
              ))}
            </div>
            <div className={styles.toolsHint}>
              Puedes ver los recursos reales del servidor en los badges de la barra superior
            </div>
          </div>

        </div>

        {/* Footer */}
        <div className={styles.footer}>
          <button className={styles.startBtn} onClick={handleClose}>
            ¡COMENZAR!
          </button>
        </div>

      </div>
    </div>
  )
}
