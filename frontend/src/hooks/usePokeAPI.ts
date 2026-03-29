import type { EvoStep, MoveEntry, PokemonData, Weakness } from '../types'

interface EvoChainNode {
  species: { name: string; url: string }
  evolves_to: EvoChainNode[]
}

export async function fetchPokemonStructured(
  name: string
): Promise<PokemonData | null> {
  try {
    const cleanName = name.trim().toLowerCase().replace(/\s+/g, '-')

    // Fetch base data and species data in parallel
    const [pokeRes, speciesRes] = await Promise.all([
      fetch(`https://pokeapi.co/api/v2/pokemon/${cleanName}`),
      fetch(`https://pokeapi.co/api/v2/pokemon-species/${cleanName}`),
    ])
    if (!pokeRes.ok) return null

    const poke = (await pokeRes.json()) as {
      id: number
      name: string
      height: number
      weight: number
      sprites: { front_default: string }
      types: Array<{ type: { name: string } }>
      stats: Array<{ stat: { name: string }; base_stat: number }>
      moves: Array<{ move: { name: string } }>
    }
    const species = speciesRes.ok
      ? ((await speciesRes.json()) as {
          flavor_text_entries: Array<{
            flavor_text: string
            language: { name: string }
          }>
          evolution_chain?: { url: string }
        })
      : null

    // Dex entry
    let dex_entry = ''
    if (species) {
      const es = species.flavor_text_entries.find(
        (e) => e.language.name === 'es'
      )
      const en = species.flavor_text_entries.find(
        (e) => e.language.name === 'en'
      )
      dex_entry = (es ?? en ?? { flavor_text: '' }).flavor_text
        .replace(/\f/g, ' ')
        .replace(/\n/g, ' ')
    }

    // Evolution chain
    let evolution_chain: EvoStep[] = []
    if (species?.evolution_chain) {
      try {
        const evoRes = await fetch(species.evolution_chain.url)
        const evoData = (await evoRes.json()) as {
          chain: EvoChainNode
        }
        const walk = (chain: EvoChainNode) => {
          const parts = chain.species.url.split('/').filter(Boolean)
          const id = parts[parts.length - 1]
          evolution_chain.push({
            name: chain.species.name,
            sprite: `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/${id}.png`,
          })
          if (chain.evolves_to?.length) walk(chain.evolves_to[0])
        }
        walk(evoData.chain)
      } catch {
        evolution_chain = []
      }
    }

    // Type effectiveness (weaknesses)
    const types = poke.types.map((t) => t.type.name)
    const weaknesses: Weakness[] = []

    for (const t of types) {
      try {
        const tr = await fetch(`https://pokeapi.co/api/v2/type/${t}`)
        const td = (await tr.json()) as {
          damage_relations: {
            double_damage_from: Array<{ name: string }>
            half_damage_from: Array<{ name: string }>
          }
        }
        td.damage_relations.double_damage_from.forEach((dt) => {
          if (!weaknesses.find((w) => w.type === dt.name))
            weaknesses.push({ type: dt.name, multiplier: 2 })
        })
        td.damage_relations.half_damage_from.forEach((dt) => {
          const ex = weaknesses.find((w) => w.type === dt.name)
          if (ex) ex.multiplier /= 2
          else weaknesses.push({ type: dt.name, multiplier: 0.5 })
        })
      } catch {
        // ignore individual type fetch failures
      }
    }

    // Stats map
    const statNameMap: Record<string, keyof typeof stats> = {
      hp:               'hp',
      attack:           'atk',
      defense:          'def',
      'special-attack': 'spatk',
      'special-defense':'spdef',
      speed:            'spd',
    }
    const stats = { hp: 0, atk: 0, def: 0, spatk: 0, spdef: 0, spd: 0 }
    poke.stats.forEach((s) => {
      const key = statNameMap[s.stat.name]
      if (key) stats[key] = s.base_stat
    })

    const moves: MoveEntry[] = poke.moves
      .slice(0, 8)
      .map((m) => ({ name: m.move.name }))

    return {
      name:            poke.name,
      number:          poke.id,
      types,
      height:          (poke.height / 10).toFixed(1) + 'm',
      weight:          (poke.weight / 10).toFixed(1) + 'kg',
      sprite:          poke.sprites.front_default,
      stats,
      moves,
      dex_entry,
      evolution_chain,
      weaknesses: weaknesses.filter((w) => w.multiplier >= 2),
    }
  } catch {
    return null
  }
}
