import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import useStore from '../../../../../store/useStore'
import styles from './DexterTab.module.css'

export default function DexterTab() {
  const agentResponse = useStore((s) => s.agentResponse)

  if (!agentResponse) {
    return (
      <div className={styles.tabContent} id="tab-dexter">
        <div className={styles.empty}>
          ejecuta una consulta<br />para ver el análisis<br />de Dexter
        </div>
      </div>
    )
  }

  return (
    <div className={`${styles.tabContent} md-content`} id="tab-dexter">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {agentResponse}
      </ReactMarkdown>
    </div>
  )
}
