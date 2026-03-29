import useStore from '../../store/useStore'
import styles from './ProviderSelect.module.css'

export default function ProviderSelect() {
  const providers = useStore((s) => s.providers)
  const selectedProvider = useStore((s) => s.selectedProvider)
  const setSelectedProvider = useStore((s) => s.setSelectedProvider)

  if (providers.length === 0) return null

  return (
    <select
      className={styles.providerSelect}
      value={selectedProvider}
      onChange={(e) => setSelectedProvider(e.target.value)}
      title="Proveedor LLM"
    >
      {providers.map((p) => (
        <option key={p.name} value={p.name}>
          {p.name}
        </option>
      ))}
    </select>
  )
}
