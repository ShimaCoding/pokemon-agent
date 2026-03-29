import styles from './Layout.module.css'
import TitleBar from '../TitleBar/TitleBar'
import SettingsModal from '../SettingsModal/SettingsModal'
import PokedexViewer from '../PokedexViewer/PokedexViewer'
import BottomControlBar from '../BottomControlBar/BottomControlBar'
import useStore from '../../store/useStore'

export default function Layout() {
  const preQuery = useStore((s) => s.preQuery)

  return (
    <div className={`${styles.app} ${preQuery ? styles.preQuery : ''}`}>
      <TitleBar />
      <SettingsModal />
      <PokedexViewer />
      <BottomControlBar />
    </div>
  )
}
