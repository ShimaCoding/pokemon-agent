import useStore from '../../../../store/useStore'
import type { TabKey } from '../../../../types'
import styles from './TabNavigation.module.css'

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'info',    label: 'INFO'    },
  { key: 'stats',   label: 'STATS'   },
  { key: 'moves',   label: 'MOVES'   },
  { key: 'evos',    label: 'EVOS'    },
  { key: 'dexter',  label: 'DEXTER'  },
  { key: 'consola', label: 'CONSOLA' },
]

export default function TabNavigation() {
  const activeTab  = useStore((s) => s.activeTab)
  const setActiveTab = useStore((s) => s.setActiveTab)

  return (
    <div
      id="tab-bar"
      className={styles.tabBar}
      role="tablist"
      aria-label="Secciones del Pokédex"
    >
      {TABS.flatMap(({ key, label }) => [
        activeTab === key
          ? <span key={`arrow-${key}`} className={styles.tabArrow} aria-hidden="true">▶</span>
          : null,
        <button
          key={key}
          className={[
            styles.tabBtn,
            activeTab === key ? styles.active : '',
          ].filter(Boolean).join(' ')}
          role="tab"
          aria-selected={activeTab === key}
          aria-controls={`tabpanel-${key}`}
          data-tab={key}
          onClick={() => setActiveTab(key)}
        >
          {label}
        </button>,
      ])}
    </div>
  )
}
