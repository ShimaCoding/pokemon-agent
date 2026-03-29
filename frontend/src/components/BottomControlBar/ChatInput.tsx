import { forwardRef } from 'react'
import useStore from '../../store/useStore'
import styles from './ChatInput.module.css'

interface Props {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  onSlashOpen: (filter: string) => void
  onSlashClose: () => void
  slashMenuOpen: boolean
}

const ChatInput = forwardRef<HTMLInputElement, Props>(function ChatInput(
  { value, onChange, onSubmit, onSlashOpen, onSlashClose, slashMenuOpen },
  ref
) {
  const inFlight = useStore((s) => s.inFlight)

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value
    onChange(v)
    if (v.startsWith('/')) {
      onSlashOpen(v.slice(1).toLowerCase())
    } else if (slashMenuOpen) {
      onSlashClose()
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !slashMenuOpen) {
      e.preventDefault()
      onSubmit()
    }
    if (e.key === 'Escape' && slashMenuOpen) {
      onSlashClose()
    }
  }

  return (
    <input
      ref={ref}
      type="text"
      className={styles.chatInput}
      placeholder="Pregunta sobre un Pokémon o escribe / para comandos…"
      value={value}
      onChange={handleChange}
      onKeyDown={handleKeyDown}
      disabled={inFlight}
      autoComplete="off"
      spellCheck={false}
    />
  )
})

export default ChatInput
