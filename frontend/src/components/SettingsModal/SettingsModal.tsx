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

  // On open: populate input with current key
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

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') handleSave()
    if (e.key === 'Escape') setSettingsOpen(false)
  }

  if (!settingsOpen) return null

  return (
    <div className={styles.modal} id="settings-modal">
      <div
        className={styles.overlay}
        onClick={() => setSettingsOpen(false)}
      />
      <div className={styles.box}>
        <div className={styles.title}>AJUSTES</div>
        <label className={styles.label} htmlFor="api-key-input">API KEY</label>
        <input
          ref={inputRef}
          id="api-key-input"
          type="password"
          className={styles.input}
          placeholder="dejar vacío si no se requiere"
          autoComplete="off"
          onKeyDown={handleKeyDown}
        />
        <div className={styles.hint}>
          Se guarda en localStorage. Enviar como cabecera X-API-Key.
        </div>
        <div className={styles.actions}>
          <button className={styles.clearBtn} onClick={handleClear}>BORRAR</button>
          <button className={styles.cancelBtn} onClick={() => setSettingsOpen(false)}>CANCELAR</button>
          <button className={styles.saveBtn} onClick={handleSave}>GUARDAR</button>
        </div>
      </div>
    </div>
  )
}
