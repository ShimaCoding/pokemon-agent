import type { PokemonData } from '../../../../../types'
import styles from './MovesTab.module.css'

interface Props { data: PokemonData | null }

export default function MovesTab({ data }: Props) {
  return (
    <div className={styles.tabContent} id="tab-moves">
      <div className={styles.movesGrid}>
        {!data || data.moves.length === 0 ? (
          <div className={styles.empty}>
            consulta un pokémon para ver sus movimientos
          </div>
        ) : (
          data.moves.slice(0, 8).map((m, i) => (
            <div key={i} className={styles.moveItem}>
              <div className={styles.moveName}>{m.name.toUpperCase()}</div>
              {m.type && <div className={styles.moveMeta}>{m.type}</div>}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
