import { useEffect, useRef, useState } from 'react'
import { useSettings } from '../../store/useStore'
import styles from './McpPopover.module.css'

/** What the connected MCP server offers: tools (marking those with a widget), prompts and resources. */
export default function McpPopover() {
  const tools = useSettings((s) => s.mcpTools)
  const resources = useSettings((s) => s.mcpResources)
  const prompts = useSettings((s) => s.prompts).filter((p) => p.isMcp)
  const loaded = useSettings((s) => s.mcpLoaded)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onMouseDown(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const online = loaded && tools.length > 0

  return (
    <div ref={ref} className={styles.root}>
      <button className={styles.chip} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={styles.status} data-online={online} aria-hidden="true" />
        MCP{loaded ? ` · ${tools.length} tools` : ''}
      </button>
      {open && (
        <div className={styles.panel} role="dialog" aria-label="Servidor MCP">
          {!online && <p className={styles.empty}>{loaded ? 'Servidor MCP no disponible.' : 'Conectando…'}</p>}
          {tools.length > 0 && (
            <section>
              <h3 className={styles.title}>Herramientas</h3>
              {tools.map((t) => (
                <div key={t.name} className={styles.item}>
                  <span className={styles.name}>
                    {t.name}
                    {t.ui_resource_uri && <span className={styles.badge}>widget</span>}
                  </span>
                  {t.description && <span className={styles.desc}>{t.description.split('\n')[0]}</span>}
                </div>
              ))}
            </section>
          )}
          {prompts.length > 0 && (
            <section>
              <h3 className={styles.title}>Prompts · escribe / en el chat</h3>
              {prompts.map((p) => (
                <div key={p.name} className={styles.item}>
                  <span className={styles.name}>{p.name}</span>
                </div>
              ))}
            </section>
          )}
          {resources.length > 0 && (
            <section>
              <h3 className={styles.title}>Recursos</h3>
              {resources.map((r) => (
                <div key={r.uri} className={styles.item}>
                  <span className={styles.name}>{r.uri}</span>
                </div>
              ))}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
