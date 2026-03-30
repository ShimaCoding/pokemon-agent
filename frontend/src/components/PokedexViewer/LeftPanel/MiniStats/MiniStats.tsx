import type { PokemonStat } from '../../../../types'
import styles from './MiniStats.module.css'

interface Props {
  stats?: PokemonStat
  types?: string[]
  number?: number
}

const MINI_STAT_KEYS: Array<{ key: keyof PokemonStat; label: string }> = [
  { key: 'hp',    label: 'HP'   },
  { key: 'atk',   label: 'ATK'  },
  { key: 'def',   label: 'DEF'  },
  { key: 'spatk', label: 'SATK' },
  { key: 'spdef', label: 'SDEF' },
  { key: 'spd',   label: 'SPD'  },
]

const CELLS = 12
const MAX_STAT = 255

const TYPE_COLORS: Record<string, { bg: string; border: string; color: string }> = {
  normal:   { bg: '#a8a878', border: '#6d6d4e', color: '#1a1a1a' },
  fire:     { bg: '#d03800', border: '#8a2800', color: '#fff'    },
  water:    { bg: '#4070c8', border: '#2040a0', color: '#fff'    },
  grass:    { bg: '#4a8a2a', border: '#2d5518', color: '#fff'    },
  electric: { bg: '#c0a000', border: '#806800', color: '#1a1a1a' },
  ice:      { bg: '#5a9e9e', border: '#3a7070', color: '#fff'    },
  fighting: { bg: '#802020', border: '#501010', color: '#fff'    },
  poison:   { bg: '#703070', border: '#501050', color: '#fff'    },
  ground:   { bg: '#a08040', border: '#705820', color: '#fff'    },
  flying:   { bg: '#7860d0', border: '#5040a0', color: '#fff'    },
  psychic:  { bg: '#c02858', border: '#881838', color: '#fff'    },
  bug:      { bg: '#6d7a00', border: '#4a5400', color: '#fff'    },
  rock:     { bg: '#8a7018', border: '#5c4a10', color: '#fff'    },
  ghost:    { bg: '#604880', border: '#402860', color: '#fff'    },
  dragon:   { bg: '#4010b0', border: '#280880', color: '#fff'    },
  dark:     { bg: '#4a3d32', border: '#2e2618', color: '#fff'    },
  steel:    { bg: '#8a8aa0', border: '#606078', color: '#fff'    },
  fairy:    { bg: '#c06070', border: '#904050', color: '#fff'    },
}

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

export default function MiniStats({ stats, types, number }: Props) {
  const numStr = number != null ? `#${String(number).padStart(3, '0')}` : null

  return (
    <div id="stats-area" className={styles.statsArea}>
      {(types != null || numStr != null) && (
        <div className={styles.infoRow}>
          <div className={styles.typeBadges}>
            {types?.map((t) => {
              const c = TYPE_COLORS[t.toLowerCase()]
              return (
                <span
                  key={t}
                  className={styles.typeBadge}
                  style={c ? { background: c.bg, borderColor: c.border, color: c.color } : undefined}
                >
                  {t.toUpperCase()}
                </span>
              )
            })}
          </div>
          {numStr && <span className={styles.numberBadge}>{numStr}</span>}
        </div>
      )}
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
