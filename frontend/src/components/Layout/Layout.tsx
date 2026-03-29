import styles from './Layout.module.css'
import TitleBar from '../TitleBar/TitleBar'
import SettingsModal from '../SettingsModal/SettingsModal'
import ModeToggle from '../ModeToggle/ModeToggle'
import PokedexViewer from '../PokedexViewer/PokedexViewer'
import BottomControlBar from '../BottomControlBar/BottomControlBar'
import useStore from '../../store/useStore'
import { lazy, Suspense } from 'react'

const AdvancedPanel = lazy(() => import('../AdvancedPanel/AdvancedPanel'))

export default function Layout() {
  const isAdvancedMode = useStore((s) => s.isAdvancedMode)
  const preQuery       = useStore((s) => s.preQuery)

  return (
    <div className={`${styles.app} ${preQuery ? styles.preQuery : ''}`}>
      <TitleBar />
      <SettingsModal />
      <ModeToggle />

      {isAdvancedMode ? (
        <Suspense fallback={<div className={styles.loadingPanel}>…</div>}>
          <AdvancedPanel />
        </Suspense>
      ) : (
        <PokedexViewer />
      )}

      <BottomControlBar />
    </div>
  )
}
