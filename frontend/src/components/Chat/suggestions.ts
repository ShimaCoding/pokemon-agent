export interface Suggestion {
  title: string
  prompt: string
}

/** One card per category; each page load picks a random prompt from its pool. */
const POOLS: { title: string; prompts: string[] }[] = [
  {
    title: 'Ficha Pokédex',
    prompts: [
      'Muéstrame la entrada de la Pokédex de Gengar',
      'Muéstrame la entrada de la Pokédex de Lucario',
      'Muéstrame la entrada de la Pokédex de Umbreon',
      'Muéstrame la entrada de la Pokédex de Garchomp',
      'Muéstrame la entrada de la Pokédex de Mimikyu',
      'Muéstrame la entrada de la Pokédex de Gardevoir',
      'Muéstrame la entrada de la Pokédex de Togepi',
      'Muéstrame la entrada de la Pokédex de Scizor',
    ],
  },
  {
    title: 'Comparar stats',
    prompts: [
      'Compara a Snorlax, Lapras y Dragonite',
      'Compara a Charizard, Blastoise y Venusaur',
      'Compara a Alakazam, Gengar y Machamp',
      'Compara a Tyranitar, Salamence y Metagross',
      'Compara a Jolteon, Vaporeon y Flareon',
      'Compara a Lucario, Gallade y Infernape',
      'Compara a Garchomp, Dragonite y Hydreigon',
      'Compara a Blissey, Chansey y Wobbuffet',
    ],
  },
  {
    title: 'Analizar stats',
    prompts: [
      '¿Qué tan bueno es Shuckle? Analiza sus estadísticas',
      '¿Qué tan bueno es Ninjask? Analiza sus estadísticas',
      '¿Qué tan bueno es Slaking? Analiza sus estadísticas',
      '¿Qué tan bueno es Magikarp? Analiza sus estadísticas',
      '¿Qué tan bueno es Ditto? Analiza sus estadísticas',
      '¿Qué tan bueno es Aegislash? Analiza sus estadísticas',
      '¿Qué tan bueno es Ferrothorn? Analiza sus estadísticas',
      '¿Qué tan bueno es Pikachu? Analiza sus estadísticas',
    ],
  },
  {
    title: 'Tipos',
    prompts: [
      '¿Contra qué tipos es fuerte el tipo Agua?',
      '¿Contra qué tipos es fuerte el tipo Hada?',
      '¿Contra qué tipos es fuerte el tipo Fantasma?',
      '¿Contra qué tipos es fuerte el tipo Dragón?',
      '¿Contra qué tipos es fuerte el tipo Acero?',
      '¿Contra qué tipos es fuerte el tipo Lucha?',
      '¿Contra qué tipos es fuerte el tipo Siniestro?',
      '¿Contra qué tipos es fuerte el tipo Tierra?',
    ],
  },
]

function pick<T>(items: T[]): T {
  return items[Math.floor(Math.random() * items.length)]
}

export function randomSuggestions(): Suggestion[] {
  return POOLS.map(({ title, prompts }) => ({ title, prompt: pick(prompts) }))
}
