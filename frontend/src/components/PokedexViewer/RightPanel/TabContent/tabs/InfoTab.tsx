import type { PokemonData, Weakness } from '../../../../../types'
import styles from './InfoTab.module.css'

interface Props { data: PokemonData | null }

function WeaknessChip({ w }: { w: Weakness }) {
  const cls = w.multiplier === 2
    ? styles.mult2x
    : w.multiplier === 0.5
      ? styles.multHalf
      : ''
  const prefix = w.multiplier === 2 ? '2× ' : w.multiplier === 0.5 ? '½ ' : ''
  return (
    <span className={`${styles.typeChip} ${cls}`}>
      {prefix}{w.type.toUpperCase()}
    </span>
  )
}

export default function InfoTab({ data }: Props) {
  const name   = data?.name?.toUpperCase() ?? '???'
  const entry  = data?.dex_entry ?? 'Consulta un Pokémon para ver su entrada en la Pokédex.'
  const type   = data?.types.join(' / ') ?? '--'
  const height = data?.height ?? '--'
  const weight = data?.weight ?? '--'
  const number = data?.number ? '#' + String(data.number).padStart(3, '0') : '--'

  return (
    <div className={styles.tabContent} id="tab-info">
      <div className={styles.pokemonName}>{name}</div>
      <div className={styles.dexEntry}>{entry}</div>
      <div className={styles.infoGrid}>
        <div className={styles.infoItem}>
          <span className={styles.infoKey}>Tipo</span>
          <span className={styles.infoVal}>{type}</span>
        </div>
        <div className={styles.infoItem}>
          <span className={styles.infoKey}>Altura</span>
          <span className={styles.infoVal}>{height}</span>
        </div>
        <div className={styles.infoItem}>
          <span className={styles.infoKey}>Peso</span>
          <span className={styles.infoVal}>{weight}</span>
        </div>
        <div className={styles.infoItem}>
          <span className={styles.infoKey}>Nº</span>
          <span className={styles.infoVal}>{number}</span>
        </div>
      </div>
      {data?.weaknesses && data.weaknesses.length > 0 && (
        <div className={styles.typeChips}>
          {data.weaknesses.map((w) => (
            <WeaknessChip key={w.type} w={w} />
          ))}
        </div>
      )}
    </div>
  )
}
