import { useEffect, useRef } from 'react'
import useStore from '../../store/useStore'
import styles from './SettingsModal.module.css'

export default function SettingsModal() {
  const settingsOpen    = useStore((s) => s.settingsOpen)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const apiKey          = useStore((s) => s.apiKey)
  const setApiKey       = useStore((s) => s.setApiKey)
  const clearApiKey     = useStore((s) => s.clearApiKey)

  const inputRef = useRef<HTMLInputElement>(null)
  const boxRef   = useRef<HTMLDivElement>(null)

  // On open: populate input with current key and move focus inside dialog
  useEffect(() => {
    if (settingsOpen && inputRef.current) {
      inputRef.current.value = apiKey
      inputRef.current.focus()
    }
  }, [settingsOpen, apiKey])

  // Check /config on mount to open modal if API key is required but not set
  useEffect(() => {
    async function checkConfig() {
      try {
        const cfg = await fetch('/config').then((r) => r.json()) as { require_api_key?: boolean }
        if (cfg.require_api_key && !apiKey) {
          setSettingsOpen(true)
        }
      } catch { /* ignore */ }
    }
    void checkConfig()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleSave() {
    const val = inputRef.current?.value.trim() ?? ''
    if (val) setApiKey(val)
    else clearApiKey()
    setSettingsOpen(false)
  }

  function handleClear() {
    clearApiKey()
    if (inputRef.current) inputRef.current.value = ''
    setSettingsOpen(false)
  }

  // Enter/Escape on the input field
  function handleInputKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') handleSave()
    if (e.key === 'Escape') setSettingsOpen(false)
  }

  // Focus trap + Escape for the whole dialog box
  function handleBoxKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      setSettingsOpen(false)
      return
    }
    if (e.key !== 'Tab' || !boxRef.current) return

    const focusable = Array.from(
      boxRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => !el.hasAttribute('disabled'))

    const first = focusable[0]
    const last  = focusable[focusable.length - 1]

    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault()
        last.focus()
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  if (!settingsOpen) return null

  return (
    <div className={styles.modal} id="settings-modal">
      <div
        className={styles.overlay}
        onClick={() => setSettingsOpen(false)}
      />
      {/* role="dialog" + aria-modal prevent screen readers from browsing outside */}
      <div
        ref={boxRef}
        className={styles.box}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onKeyDown={handleBoxKeyDown}
      >
        <div className={styles.title} id="settings-title">AJUSTES</div>
        <label className={styles.label} htmlFor="api-key-input">API KEY</label>
        <input
          ref={inputRef}
          id="api-key-input"
          type="password"
          className={styles.input}
          placeholder="dejar vacío si no se requiere"
          autoComplete="off"
          onKeyDown={handleInputKeyDown}
        />
        <div className={styles.hint} role="note">
          Se guarda temporalmente en sessionStorage. Se borra al cerrar la pestaña del navegador.
        </div>
        <div className={styles.actions}>
          <button
            className={styles.clearBtn}
            onClick={handleClear}
            aria-label="Borrar API Key guardada"
          >BORRAR</button>
          <button
            className={styles.cancelBtn}
            onClick={() => setSettingsOpen(false)}
            aria-label="Cancelar y cerrar ajustes"
          >CANCELAR</button>
          <button
            className={styles.saveBtn}
            onClick={handleSave}
            aria-label="Guardar API Key"
          >GUARDAR</button>
        </div>
      </div>
    </div>
  )
}
