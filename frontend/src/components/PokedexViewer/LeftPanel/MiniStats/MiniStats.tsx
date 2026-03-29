import type { PokemonStat } from '../../../../types'
import styles from './MiniStats.module.css'

interface Props {
  stats?: PokemonStat
}

const MINI_STAT_KEYS: Array<{ key: keyof PokemonStat; label: string }> = [
  { key: 'hp',  label: 'HP'  },
  { key: 'atk', label: 'ATK' },
  { key: 'def', label: 'DEF' },
  { key: 'spd', label: 'SPD' },
]

const CELLS = 12
const MAX_STAT = 255

function MiniBar({ value }: { value: number }) {
  const filled = Math.round((value / MAX_STAT) * CELLS)
  return (
    <div className={styles.barTrack}>
      {Array.from({ length: CELLS }, (_, i) => (
        <div
          key={i}
          className={`${styles.barCell} ${i < filled ? styles.filled : styles.empty}`}
        />
      ))}
    </div>
  )
}

export default function MiniStats({ stats }: Props) {
  return (
    <div id="stats-area" className={styles.statsArea}>
      {MINI_STAT_KEYS.map(({ key, label }) => {
        const value = stats?.[key] ?? 0
        return (
          <div key={key} className={styles.statRow}>
            <span className={styles.statLabel}>{label}</span>
            <MiniBar value={value} />
            <span className={styles.statValue}>{stats ? value : '--'}</span>
          </div>
        )
      })}
    </div>
  )
}
