import { useEffect, useRef } from 'react'
import { useSettings } from '../../store/useStore'
import styles from './SettingsModal.module.css'

export default function SettingsModal() {
  const settingsOpen = useSettings((s) => s.settingsOpen)
  const setSettingsOpen = useSettings((s) => s.setSettingsOpen)
  const apiKey = useSettings((s) => s.apiKey)
  const setApiKey = useSettings((s) => s.setApiKey)
  const clearApiKey = useSettings((s) => s.clearApiKey)
  const settingsDismissed = useSettings((s) => s.settingsDismissed)
  const setSettingsDismissed = useSettings((s) => s.setSettingsDismissed)

  const inputRef = useRef<HTMLInputElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  // On open: populate input with current key and move focus inside dialog
  useEffect(() => {
    if (settingsOpen && inputRef.current) {
      inputRef.current.value = apiKey
      inputRef.current.focus()
    }
  }, [settingsOpen, apiKey])

  // Open on first visit when the server requires an API key and none is set.
  useEffect(() => {
    fetch('/config')
      .then((r) => r.json() as Promise<{ require_api_key?: boolean }>)
      .then((cfg) => {
        if (cfg.require_api_key && !apiKey && !settingsDismissed) setSettingsOpen(true)
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function dismiss() {
    setSettingsDismissed(true)
    setSettingsOpen(false)
  }

  function handleSave() {
    const val = inputRef.current?.value.trim() ?? ''
    if (val) setApiKey(val)
    else clearApiKey()
    dismiss()
  }

  function handleClear() {
    clearApiKey()
    if (inputRef.current) inputRef.current.value = ''
    dismiss()
  }

  // Focus trap + Escape for the whole dialog box
  function handleBoxKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      dismiss()
      return
    }
    if (e.key !== 'Tab' || !boxRef.current) return
    const focusable = Array.from(
      boxRef.current.querySelectorAll<HTMLElement>('button, [href], input, [tabindex]:not([tabindex="-1"])'),
    ).filter((el) => !el.hasAttribute('disabled'))
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  if (!settingsOpen) return null

  return (
    <div className={styles.modal}>
      <div className={styles.overlay} onClick={dismiss} />
      <div
        ref={boxRef}
        className={styles.box}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onKeyDown={handleBoxKeyDown}
      >
        <h2 className={styles.title} id="settings-title">
          AJUSTES
        </h2>
        <label className={styles.label} htmlFor="api-key-input">
          API KEY
        </label>
        <input
          ref={inputRef}
          id="api-key-input"
          type="password"
          className={styles.input}
          placeholder="Opcional"
          autoComplete="off"
          onKeyDown={(e) => e.key === 'Enter' && handleSave()}
        />
        <p className={styles.hint}>
          Con una API key válida no hay límite de consultas y Dexter usa gpt-oss-120b. Se guarda en sessionStorage y
          se borra al cerrar la pestaña.
        </p>
        <div className={styles.actions}>
          <button className={styles.secondary} onClick={handleClear}>
            BORRAR
          </button>
          <span className={styles.spacer} />
          <button className={styles.secondary} onClick={dismiss}>
            CANCELAR
          </button>
          <button className={styles.primary} onClick={handleSave}>
            GUARDAR
          </button>
        </div>
        <p className={styles.source}>
          Código fuente:{' '}
          <a href="https://github.com/ShimaCoding/mcp-pokemon-server" target="_blank" rel="noopener noreferrer">
            mcp-pokemon-server
          </a>
          {' · '}
          <a href="https://github.com/ShimaCoding/pokemon-agent" target="_blank" rel="noopener noreferrer">
            pokemon-agent
          </a>
        </p>
      </div>
    </div>
  )
}
