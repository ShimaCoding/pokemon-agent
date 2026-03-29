import useStore from '../../store/useStore'
import styles from './SendButton.module.css'

interface Props {
  onClick: () => void
}

export default function SendButton({ onClick }: Props) {
  const inFlight = useStore((s) => s.inFlight)

  return (
    <button
      className={styles.sendButton}
      onClick={onClick}
      disabled={inFlight}
      title="Enviar consulta"
    >
      ENVIAR
    </button>
  )
}
