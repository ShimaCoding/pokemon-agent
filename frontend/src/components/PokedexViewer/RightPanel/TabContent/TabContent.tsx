import useStore from '../../../../store/useStore'
import InfoTab from './tabs/InfoTab'
import StatsTab from './tabs/StatsTab'
import MovesTab from './tabs/MovesTab'
import EvosTab from './tabs/EvosTab'
import DexterTab from './tabs/DexterTab'

export default function TabContent() {
  const activeTab   = useStore((s) => s.activeTab)
  const pokemonData = useStore((s) => s.pokemonData)

  switch (activeTab) {
    case 'info':   return <InfoTab  data={pokemonData} />
    case 'stats':  return <StatsTab data={pokemonData} />
    case 'moves':  return <MovesTab data={pokemonData} />
    case 'evos':   return <EvosTab  data={pokemonData} />
    case 'dexter': return <DexterTab />
    default:       return <InfoTab  data={pokemonData} />
  }
}
