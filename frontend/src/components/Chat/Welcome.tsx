import { useState } from 'react'
import { sendChatMessage } from '../../hooks/useAgentStream'
import styles from './Welcome.module.css'

const SUGGESTIONS = [
  { title: 'Ficha Pokédex', prompt: 'Muéstrame la entrada de la Pokédex de Gengar' },
  { title: 'Comparar stats', prompt: 'Compara a Snorlax, Lapras y Dragonite' },
  { title: 'Analizar stats', prompt: '¿Qué tan bueno es Shuckle? Analiza sus estadísticas' },
  { title: 'Tipos', prompt: '¿Contra qué tipos es fuerte el tipo Agua?' },
]

export default function Welcome() {
  const [hover, setHover] = useState(0)

  return (
    <section className={styles.welcome}>
      <div className={styles.dialog}>
        <p className={styles.line}>¡Hola, entrenador! Soy DEXTER, la Pokédex del Profesor Oak.</p>
        <p className={styles.line}>
          Pregúntame lo que quieras. Consultaré el servidor MCP y juzgaré tu pregunta con rigor científico.
        </p>
        <span className={styles.more} aria-hidden="true">▼</span>
      </div>
      <nav className={styles.menu} aria-label="Ejemplos">
        {SUGGESTIONS.map((s, i) => (
          <button
            key={s.title}
            className={styles.option}
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onClick={() => void sendChatMessage(s.prompt)}
          >
            <span className={styles.cursor}>{hover === i ? '▶' : ''}</span>
            <span className={styles.optionTitle}>{s.title}</span>
            <span className={styles.optionPrompt}>{s.prompt}</span>
          </button>
        ))}
      </nav>
      <p className={styles.hint}>Escribe / en la barra para usar los prompts del servidor MCP.</p>
    </section>
  )
}
