import { useEffect } from 'react'
import useStore from '../store/useStore'
import type { McpPrompt, SlashPrompt } from '../types'

const PROMPT_ARG_DEFAULTS: Record<string, string> = {
  pokemon_name:    'pikachu',
  pokemon1:        'pikachu',
  pokemon2:        'charizard',
  user_team:       'pikachu,charizard,bulbasaur',
  opponent_team:   'gengar,starmie,vaporeon',
  team:            'gengar,mewtwo,dragonite',
  analysis_type:   'general',
  user_level:      'beginner',
  theme:           'balanced',
  format:          'casual',
  scenario:        'learning',
  attacking_type:  'fire',
  defending_types: 'water',
  battle_format:   'singles',
  strategy_focus:  'balanced',
  analysis_depth:  'standard',
  focus_areas:     'offense',
  environment:     'neutral',
  restrictions:    '',
}

const FALLBACK_PROMPTS: SlashPrompt[] = [
  { group: 'Ejemplos', name: 'Info de Pokémon',   desc: 'Datos técnicos y stats',  template: 'Dame información de Mewtwo' },
  { group: 'Ejemplos', name: 'Análisis de stats', desc: 'Potencial de combate',     template: 'Analiza las estadísticas de Shuckle' },
  { group: 'Ejemplos', name: 'Ventaja de tipo',   desc: 'Fortalezas elementales',   template: 'Contra qué tipos es fuerte el tipo Agua?' },
  { group: 'Ejemplos', name: 'Comparativa 1v1',   desc: 'Analizar dos Pokémon',     template: '¿Quién tiene mejor ataque: Arcanine o Gyarados? Analiza a ambos.' },
]

function promptGroupLabel(name: string): string {
  const prefix = name.split('/')[0]
  const MAP: Record<string, string> = { educational: 'Educativo', battle: 'Batalla' }
  return MAP[prefix] ?? (prefix.charAt(0).toUpperCase() + prefix.slice(1))
}

function buildPromptTemplate(
  promptName: string,
  args: McpPrompt['arguments']
): string {
  if (!args || args.length === 0) return `Usa el prompt ${promptName}`
  const parts = args
    .filter((a) => a.name && PROMPT_ARG_DEFAULTS[a.name] !== '')
    .map((a) => `${a.name}=${PROMPT_ARG_DEFAULTS[a.name] ?? `[${a.name}]`}`)
  return `Usa el prompt ${promptName}` + (parts.length ? ` con ${parts.join(' ')}` : '')
}

export function usePrompts() {
  const apiKey   = useStore((s) => s.apiKey)
  const setPrompts = useStore((s) => s.setPrompts)

  useEffect(() => {
    async function load() {
      try {
        const headers: Record<string, string> = {}
        if (apiKey) headers['X-API-Key'] = apiKey

        const resp = await fetch('/api/prompts', { headers })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

        const data = (await resp.json()) as McpPrompt[]
        if (Array.isArray(data) && data.length > 0) {
          const mapped: SlashPrompt[] = data.map((p) => ({
            group:    promptGroupLabel(p.name),
            name:     p.name,
            desc:     p.description ?? 'Prompt MCP',
            template: buildPromptTemplate(p.name, p.arguments),
            isMcp:    true,
          }))
          setPrompts(mapped)
        } else {
          setPrompts(FALLBACK_PROMPTS)
        }
      } catch {
        setPrompts(FALLBACK_PROMPTS)
      }
    }
    void load()
  }, [apiKey, setPrompts])
}
