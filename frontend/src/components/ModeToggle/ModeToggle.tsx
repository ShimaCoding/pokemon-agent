import useStore from '../../store/useStore'
import styles from './ModeToggle.module.css'

export default function ModeToggle() {
  const isAdvancedMode = useStore((s) => s.isAdvancedMode)
  const toggleMode     = useStore((s) => s.toggleMode)

  const label = isAdvancedMode ? 'Modo Desarrollador (Logs)' : 'Modo Normal'

  return (
    <div className={styles.modebar} id="modebar">
      <span className={styles.modeTitle}>{label}</span>
      <div className={styles.toggleWrap}>
        <span className={styles.toggleLabel}>Consola de Dexter (Logs)</span>
        <div
          className={`${styles.toggleSwitch} ${isAdvancedMode ? styles.on : ''}`}
          id="mode-toggle"
          role="button"
          tabIndex={0}
          aria-label="Cambiar modo"
          onClick={toggleMode}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') toggleMode()
          }}
        />
        <span className={styles.toggleLabel}>{isAdvancedMode ? 'ON' : 'OFF'}</span>
      </div>
    </div>
  )
}
