import { useState } from 'react'
import styles from './JsonViewer.module.css'

/** Pretty-printed JSON (or plain text) with a copy button. */
export default function JsonViewer({ data }: { data: unknown }) {
  const [copied, setCopied] = useState(false)
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div className={styles.viewer}>
      <button className={styles.copy} onClick={copy}>
        {copied ? 'Copiado' : 'Copiar'}
      </button>
      <pre className={styles.pre}>{text}</pre>
    </div>
  )
}
