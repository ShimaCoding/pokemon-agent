import { useEffect, useRef } from 'react'
import { useChat } from '../../store/useStore'
import Composer from './Composer'
import MessageItem from './MessageItem'
import Welcome from './Welcome'
import styles from './ChatView.module.css'

// Follow the stream only while the reader is already near the bottom.
const STICK_THRESHOLD_PX = 120

export default function ChatView() {
  const messages = useChat((s) => s.messages)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  function onScroll() {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX
  }

  const empty = messages.length === 0

  // Any store change (text delta, widget, trace) may grow the page. The welcome
  // screen is read from the top, so it is never pinned to the bottom.
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current && !empty) el.scrollTop = el.scrollHeight
  })

  // A new user message always jumps to the bottom.
  const lastUserId = [...messages].reverse().find((m) => m.role === 'user')?.id
  useEffect(() => {
    stickRef.current = true
  }, [lastUserId])

  // While the chat is empty the welcome dialog is the input, so the bottom bar stays hidden.
  return (
    <main className={styles.view}>
      <div ref={scrollRef} className={styles.scroll} onScroll={onScroll}>
        <div className={`${styles.column} ${empty ? styles.empty : ''}`}>
          {empty ? <Welcome /> : messages.map((m) => <MessageItem key={m.id} message={m} />)}
        </div>
      </div>
      {!empty && <Composer />}
    </main>
  )
}
