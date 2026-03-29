import useStore from '../../../../store/useStore'
import type { TabKey } from '../../../../types'
import styles from './TabNavigation.module.css'

const TABS: Array<{ key: TabKey; label: string; mobileOnly?: boolean }> = [
  { key: 'info',    label: 'INFO'    },
  { key: 'stats',   label: 'STATS'   },
  { key: 'moves',   label: 'MOVES'   },
  { key: 'evos',    label: 'EVOS'    },
  { key: 'dexter',  label: 'DEXTER'  },
  { key: 'consola', label: 'CONSOLA', mobileOnly: true },
]

export default function TabNavigation() {
  const activeTab  = useStore((s) => s.activeTab)
  const setActiveTab = useStore((s) => s.setActiveTab)

  return (
    <div id="tab-bar" className={styles.tabBar}>
      <span className={styles.tabArrow}>▶</span>
      {TABS.map(({ key, label, mobileOnly }) => (
        <button
          key={key}
          className={[
            styles.tabBtn,
            activeTab === key ? styles.active : '',
            mobileOnly ? styles.mobileOnly : '',
          ].filter(Boolean).join(' ')}
          data-tab={key}
          onClick={() => setActiveTab(key)}
        >
          {label}
        </button>
      ))}
    </div>
  )
}
