import styles from './DexterAvatar.module.css'

/** Pokédex lens: Dexter's face. Pulses while it's "thinking". */
export default function DexterAvatar({ active = false }: { active?: boolean }) {
  return (
    <span className={styles.avatar} data-active={active} aria-hidden="true">
      <svg viewBox="0 0 32 32" width="32" height="32">
        <circle cx="16" cy="16" r="15" className={styles.shell} />
        <circle cx="16" cy="16" r="8.5" className={styles.ring} />
        <circle cx="16" cy="16" r="6" className={styles.lens} />
        <circle cx="13.6" cy="13.6" r="1.8" className={styles.glint} />
      </svg>
    </span>
  )
}
