import type { PokemonData } from '../../../../../types'
import styles from './EvosTab.module.css'

interface Props { data: PokemonData | null }

export default function EvosTab({ data }: Props) {
  const evos = data?.evolution_chain ?? []

  return (
    <div className={styles.tabContent} id="tab-evos">
      <div className={styles.evoChain}>
        {evos.length === 0 ? (
          <div className={styles.empty}>
            consulta un pokémon<br />para ver su cadena<br />evolutiva
          </div>
        ) : (
          evos.map((evo, i) => (
            <div key={evo.name} className={styles.evoGroup}>
              {i > 0 && <span className={styles.evoArrow}>→</span>}
              <div className={styles.evoPokemon}>
                <div className={styles.evoFrame}>
                  {evo.sprite ? (
                    <img
                      src={evo.sprite}
                      alt={evo.name}
                      className={styles.evoSprite}
                    />
                  ) : (
                    <div className={styles.evoNoSprite}>
                      {evo.name.slice(0, 4)}
                    </div>
                  )}
                </div>
                <span className={styles.evoName}>{evo.name.toUpperCase()}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
