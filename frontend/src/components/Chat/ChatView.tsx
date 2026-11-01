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

  // Any store change (text delta, widget, trace) may grow the page.
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  })

  // A new user message always jumps to the bottom.
  const lastUserId = [...messages].reverse().find((m) => m.role === 'user')?.id
  useEffect(() => {
    stickRef.current = true
  }, [lastUserId])

  return (
    <main className={styles.view}>
      <div ref={scrollRef} className={styles.scroll} onScroll={onScroll}>
        <div className={styles.column}>
          {messages.length === 0 ? <Welcome /> : messages.map((m) => <MessageItem key={m.id} message={m} />)}
        </div>
      </div>
      <Composer />
    </main>
  )
}
