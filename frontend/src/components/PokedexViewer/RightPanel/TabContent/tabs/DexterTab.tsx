import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import useStore from '../../../../../store/useStore'
import { LOADING_PHRASES } from '../../../../../hooks/useAgentStream'
import styles from './DexterTab.module.css'

export default function DexterTab() {
  const agentResponse = useStore((s) => s.agentResponse)
  const inFlight      = useStore((s) => s.inFlight)
  const [phraseIdx, setPhraseIdx] = useState(() => Math.floor(Math.random() * LOADING_PHRASES.length))

  useEffect(() => {
    if (!inFlight || agentResponse) return
    const timer = setInterval(() => {
      setPhraseIdx((i) => (i + 1) % LOADING_PHRASES.length)
    }, 2200)
    return () => clearInterval(timer)
  }, [inFlight, agentResponse])

  if (!agentResponse) {
    return (
      <div className={styles.tabContent} id="tab-dexter">
        <div className={styles.empty}>
          {inFlight
            ? LOADING_PHRASES[phraseIdx]
            : <>ejecuta una consulta<br />para ver el análisis<br />de Dexter</>
          }
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
