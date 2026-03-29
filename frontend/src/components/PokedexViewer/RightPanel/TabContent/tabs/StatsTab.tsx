import type { PokemonData, PokemonStat } from '../../../../../types'
import styles from './StatsTab.module.css'

interface Props { data: PokemonData | null }

const BIG_STATS: Array<{ key: keyof PokemonStat; label: string }> = [
  { key: 'hp',    label: 'HP'     },
  { key: 'atk',   label: 'ATK'    },
  { key: 'def',   label: 'DEF'    },
  { key: 'spatk', label: 'SP.ATK' },
  { key: 'spdef', label: 'SP.DEF' },
  { key: 'spd',   label: 'SPD'    },
]

const MAX_STAT = 255

export default function StatsTab({ data }: Props) {
  return (
    <div className={styles.tabContent} id="tab-stats">
      {BIG_STATS.map(({ key, label }) => {
        const value = data?.stats?.[key] ?? 0
        const pct   = Math.min(100, (value / MAX_STAT) * 100)
        return (
          <div key={key} className={styles.statRow}>
            <span className={styles.statLabel}>{label}</span>
            <div className={styles.barTrack}>
              <div
                className={styles.barFill}
                style={{ width: data ? `${pct}%` : '0%' }}
              />
            </div>
            <span className={styles.statVal}>{data ? value : '--'}</span>
          </div>
        )
      })}
    </div>
  )
}
