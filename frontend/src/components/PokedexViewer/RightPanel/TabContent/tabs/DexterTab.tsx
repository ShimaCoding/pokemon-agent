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
  const [displayedText, setDisplayedText] = useState('')

  useEffect(() => {
    if (!agentResponse) {
      setDisplayedText('')
      return
    }

    // Safety: Si el store se reseteó y trajo texto nuevo que es más corto
    if (agentResponse.length < displayedText.length) {
      setDisplayedText('')
      return
    }

    if (displayedText.length < agentResponse.length) {
      // 3 chars por frame a 60fps = ~180 chars/segundo (muy fluido para leer)
      const id = requestAnimationFrame(() => {
        setDisplayedText(agentResponse.slice(0, displayedText.length + 3))
      })
      return () => cancelAnimationFrame(id)
    }
  }, [agentResponse, displayedText])

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
      {/* SECURITY (Finding 7): Do NOT add rehype-raw here. That plugin enables
          raw HTML rendering, which would create a direct XSS vector from
          LLM-generated content (prompt-injected <script> or <img onerror>). */}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {displayedText}
      </ReactMarkdown>
    </div>
  )
}
