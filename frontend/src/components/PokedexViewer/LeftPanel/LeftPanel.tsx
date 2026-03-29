import styles from './LeftPanel.module.css'
import SearchBar from './SearchBar/SearchBar'
import SpriteDisplay from './SpriteDisplay/SpriteDisplay'
import MiniStats from './MiniStats/MiniStats'
import DexterConsole from '../../DexterConsole/DexterConsole'
import useStore from '../../../store/useStore'

export default function LeftPanel() {
  const pokemonData = useStore((s) => s.pokemonData)

  return (
    <div id="left-panel" className={styles.leftPanel}>
      <SearchBar />
      <SpriteDisplay sprite={pokemonData?.sprite} name={pokemonData?.name} />
      <MiniStats stats={pokemonData?.stats} />
      <DexterConsole collapsible />
    </div>
  )
}
