import styles from './Layout.module.css'
import TitleBar from '../TitleBar/TitleBar'
import SettingsModal from '../SettingsModal/SettingsModal'
import IntroModal from '../IntroModal/IntroModal'
import PokedexViewer from '../PokedexViewer/PokedexViewer'
import BottomControlBar from '../BottomControlBar/BottomControlBar'
import WikiMode from '../WikiMode/WikiMode'
import useStore from '../../store/useStore'

export default function Layout() {
  const preQuery = useStore((s) => s.preQuery)
  const uiMode = useStore((s) => s.uiMode)

  return (
    <div className={`${styles.app} ${preQuery ? styles.preQuery : ''}`}>
      <TitleBar />
      <SettingsModal />
      <IntroModal />
      {uiMode === 'learn' ? <WikiMode /> : <PokedexViewer />}
      <BottomControlBar />
    </div>
  )
}
