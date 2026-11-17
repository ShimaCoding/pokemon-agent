import { useEffect } from 'react'
import { useChat } from '../../store/useStore'
import styles from './UndoToast.module.css'

const VISIBLE_MS = 8000

/** "Conversación nueva · Deshacer" after "Nueva", so a misclick never loses a chat. */
export default function UndoToast() {
  const cleared = useChat((s) => s.cleared)
  const undo = useChat((s) => s.undoNewConversation)
  const dismiss = useChat((s) => s.dismissCleared)

  useEffect(() => {
    if (!cleared) return
    const timer = window.setTimeout(dismiss, VISIBLE_MS)
    return () => window.clearTimeout(timer)
  }, [cleared, dismiss])

  if (!cleared) return null

  return (
    <div className={styles.toast} role="status">
      <span>Conversación nueva</span>
      <button className={styles.undo} onClick={undo}>
        Deshacer
      </button>
      <button className={styles.close} onClick={dismiss} aria-label="Cerrar aviso">
        ×
      </button>
    </div>
  )
}
