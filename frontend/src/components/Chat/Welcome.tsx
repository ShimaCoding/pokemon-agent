import { sendChatMessage } from '../../hooks/useAgentStream'
import styles from './Welcome.module.css'

const SUGGESTIONS = [
  { title: 'Ficha Pokédex', prompt: 'Muéstrame la entrada de la Pokédex de Gengar' },
  { title: 'Comparar stats', prompt: 'Compara a Snorlax, Lapras y Dragonite' },
  { title: 'Analizar stats', prompt: '¿Qué tan bueno es Shuckle? Analiza sus estadísticas' },
  { title: 'Tipos', prompt: '¿Contra qué tipos es fuerte el tipo Agua?' },
]

export default function Welcome() {
  return (
    <section className={styles.welcome}>
      <h1 className={styles.greeting}>
        <span className={styles.hello}>Hola, entrenador.</span>
        <span className={styles.sub}>Pregunta. Dexter juzgará tu pregunta con rigor científico.</span>
      </h1>
      <div className={styles.grid}>
        {SUGGESTIONS.map((s) => (
          <button key={s.title} className={styles.card} onClick={() => void sendChatMessage(s.prompt)}>
            <span className={styles.cardTitle}>{s.title}</span>
            <span className={styles.cardPrompt}>{s.prompt}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
