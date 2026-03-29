import styles from './PokedexViewer.module.css'
import LeftPanel from './LeftPanel/LeftPanel'
import RightPanel from './RightPanel/RightPanel'

export default function PokedexViewer() {
  return (
    <div id="normal-panel" className={styles.normalPanel}>
      <div id="content" className={styles.content}>
        <LeftPanel />
        <RightPanel />
      </div>
    </div>
  )
}
