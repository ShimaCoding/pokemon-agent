import styles from './SpriteDisplay.module.css'

interface Props {
  sprite?: string
  name?: string
}

export default function SpriteDisplay({ sprite, name }: Props) {
  return (
    <div id="sprite-area" className={styles.spriteArea}>
      <div className={styles.spriteFrame} id="sprite-frame">
        {sprite ? (
          <img
            id="pokemon-sprite"
            className={styles.sprite}
            src={sprite}
            alt={name ?? ''}
          />
        ) : (
          <div id="sprite-placeholder" className={styles.placeholder}>
            esperando<br />análisis<br />del pokémon
          </div>
        )}
      </div>
    </div>
  )
}
