import { useState } from 'react'
import useStore from '../../../../store/useStore'
import { useAgentStream } from '../../../../hooks/useAgentStream'
import { fetchPokemonStructured } from '../../../../hooks/usePokeAPI'
import { getRandomLoadingPhrase } from '../../../../hooks/useAgentStream'
import styles from './SearchBar.module.css'

export default function SearchBar() {
  const [value, setValue]   = useState('')
  const inFlight            = useStore((s) => s.inFlight)
  const setPokemonData      = useStore((s) => s.setPokemonData)
  const { runQuery }        = useAgentStream()

  async function quickSearch(name: string) {
    const clean = name.trim()
    if (!clean || inFlight) return

    // 1. Load visual data immediately (sprite + bars)
    const data = await fetchPokemonStructured(clean)
    if (data) setPokemonData(data)

    // 2. Invoke Dexter agent
    await runQuery(`Analiza al Pokémon ${clean}.`)
    setValue('')
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    void quickSearch(value)
  }

  return (
    <form id="search-row" className={styles.searchRow} onSubmit={handleSubmit}>
      <span className={styles.searchIcon}>🔍</span>
      <input
        id="search-input"
        className={styles.searchInput}
        type="text"
        placeholder={inFlight ? getRandomLoadingPhrase() : 'buscar pokémon por nombre o número...'}
        maxLength={30}
        value={value}
        disabled={inFlight}
        onChange={(e) => setValue(e.target.value)}
      />
      <button
        id="search-btn"
        className={styles.searchBtn}
        type="submit"
        disabled={inFlight || !value.trim()}
      >
        ▶
      </button>
    </form>
  )
}
