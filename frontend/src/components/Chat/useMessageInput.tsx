import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { useChat } from '../../store/useStore'
import SlashMenu from './SlashMenu'

const MAX_LENGTH = 500

interface Options {
  onSend: (query: string) => void
  /** Keep the text after sending (the welcome box unmounts and flies away with it). */
  keepValueOnSend?: boolean
  slashPlacement?: 'above' | 'below'
}

/**
 * Shared state for the chat inputs (bottom bar and welcome dialog): autosize,
 * Enter to send / Shift+Enter for a new line, and the `/` prompt menu.
 */
export function useMessageInput({ onSend, keepValueOnSend = false, slashPlacement = 'above' }: Options) {
  const inFlight = useChat((s) => s.inFlight)
  const rateLimitSeconds = useChat((s) => s.rateLimitSeconds)
  const [value, setValue] = useState('')
  const [slashFilter, setSlashFilter] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const blocked = inFlight || rateLimitSeconds > 0
  const canSend = value.trim().length > 0 && !blocked

  // Grow with the content up to the CSS max-height; only then show a scrollbar.
  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
    el.style.overflowY = el.scrollHeight > el.clientHeight ? 'auto' : 'hidden'
  }, [value])

  const submit = useCallback(() => {
    if (!canSend) return
    if (!keepValueOnSend) setValue('')
    setSlashFilter(null)
    onSend(value)
  }, [canSend, keepValueOnSend, onSend, value])

  function onChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const v = e.target.value
    setValue(v)
    setSlashFilter(v.startsWith('/') ? v.slice(1).toLowerCase() : null)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // The slash menu owns Enter/arrows/Escape while it's open.
    if (slashFilter !== null) return
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const onSlashSelect = useCallback((template: string) => {
    setValue(template)
    setSlashFilter(null)
    inputRef.current?.focus()
  }, [])
  const onSlashClose = useCallback(() => setSlashFilter(null), [])

  const slashMenu =
    slashFilter !== null ? (
      <SlashMenu
        key={slashFilter}
        filter={slashFilter}
        placement={slashPlacement}
        onSelect={onSlashSelect}
        onClose={onSlashClose}
      />
    ) : null

  return {
    inFlight,
    rateLimitSeconds,
    canSend,
    submit,
    slashMenu,
    inputRef,
    inputProps: { ref: inputRef, value, onChange, onKeyDown, maxLength: MAX_LENGTH, rows: 1 },
  }
}
