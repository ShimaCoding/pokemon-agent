import styles from './RightPanel.module.css'
import TabNavigation from './TabNavigation/TabNavigation'
import TabContent from './TabContent/TabContent'

export default function RightPanel() {
  return (
    <div id="right-panel" className={styles.rightPanel}>
      <TabNavigation />
      <TabContent />
    </div>
  )
}
