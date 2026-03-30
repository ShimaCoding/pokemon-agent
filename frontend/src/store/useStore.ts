import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { McpResource, McpTool, PokemonData, Provider, SlashPrompt, TabKey, TraceEvent } from '../types'

// ── Types ────────────────────────────────────────────────────────

interface AppState {
  // API Key (persisted)
  apiKey: string
  setApiKey: (key: string) => void
  clearApiKey: () => void

  // Dev mode toggle (persisted): true=consola, false=pokedex/dexter
  devMode: boolean
  setDevMode: (v: boolean) => void

  // Settings modal
  settingsOpen: boolean
  setSettingsOpen: (open: boolean) => void

  // Intro modal
  introOpen: boolean
  setIntroOpen: (open: boolean) => void
  introDismissed: boolean
  setIntroDismissed: (dismissed: boolean) => void

  // Query draft (pre-fill input from intro modal examples)
  queryDraft: string
  setQueryDraft: (q: string) => void
  clearQueryDraft: () => void

  // Provider list & selection
  providers: Provider[]
  setProviders: (providers: Provider[]) => void
  selectedProvider: string
  setSelectedProvider: (name: string) => void

  // Prompts (slash menu)
  prompts: SlashPrompt[]
  setPrompts: (prompts: SlashPrompt[]) => void

  // MCP capabilities
  mcpTools: McpTool[]
  setMcpTools: (tools: McpTool[]) => void
  mcpResources: McpResource[]
  setMcpResources: (resources: McpResource[]) => void

  // Active tab
  activeTab: TabKey
  setActiveTab: (tab: TabKey) => void

  // Pokémon data panel
  pokemonData: PokemonData | null
  setPokemonData: (data: PokemonData | null) => void

  // Agent stream response (markdown)
  agentResponse: string
  appendText: (delta: string) => void

  // Trace logs
  traceLogs: TraceEvent[]
  appendTraceLog: (event: TraceEvent) => void

  // In-flight status (for disabling UI inputs)
  inFlight: boolean
  setInFlight: (v: boolean) => void

  // Session reset (between queries)
  resetSession: () => void

  // Pre-query mobile state
  preQuery: boolean
  setPreQuery: (v: boolean) => void
}

// ── Store ────────────────────────────────────────────────────────

// The apiKey is persisted in localStorage; everything else is ephemeral.
const useStore = create<AppState>()(
  persist(
    (set) => ({
      // API Key
      apiKey: '',
      setApiKey: (key) => set({ apiKey: key }),
      clearApiKey: () => set({ apiKey: '' }),

      // Dev mode
      devMode: true,
      setDevMode: (v) => set({ devMode: v }),

      // Settings modal
      settingsOpen: false,
      setSettingsOpen: (open) => set({ settingsOpen: open }),

      // Intro modal
      introOpen: false,
      setIntroOpen: (open) => set({ introOpen: open }),
      introDismissed: false,
      setIntroDismissed: (dismissed) => set({ introDismissed: dismissed }),

      // Query draft
      queryDraft: '',
      setQueryDraft: (q) => set({ queryDraft: q }),
      clearQueryDraft: () => set({ queryDraft: '' }),

      // Providers
      providers: [],
      setProviders: (providers) => set({ providers }),
      selectedProvider: '',
      setSelectedProvider: (name) => set({ selectedProvider: name }),

      // Prompts
      prompts: [],
      setPrompts: (prompts) => set({ prompts }),

      // MCP capabilities
      mcpTools: [],
      setMcpTools: (mcpTools) => set({ mcpTools }),
      mcpResources: [],
      setMcpResources: (mcpResources) => set({ mcpResources }),

      // Active tab
      activeTab: 'info',
      setActiveTab: (tab) => set({ activeTab: tab }),

      // Pokémon data
      pokemonData: null,
      setPokemonData: (data) => set({ pokemonData: data }),

      // Agent response text (streaming markdown)
      agentResponse: '',
      appendText: (delta) =>
        set((s) => ({ agentResponse: s.agentResponse + delta })),

      // Trace logs
      traceLogs: [],
      appendTraceLog: (event) =>
        set((s) => ({ traceLogs: [...s.traceLogs, event] })),

      // In-flight
      inFlight: false,
      setInFlight: (v) => set({ inFlight: v }),

      // Session reset (tab navigation handled by useAgentStream based on devMode)
      resetSession: () =>
        set({ agentResponse: '', traceLogs: [] }),

      // Pre-query (mobile)
      preQuery: true,
      setPreQuery: (v) => set({ preQuery: v }),
    }),
    {
      name: 'mcpokedex-storage',
      // Persist API key, intro dismissed flag, and dev mode
      partialize: (state) => ({ apiKey: state.apiKey, introDismissed: state.introDismissed, devMode: state.devMode }),
    }
  )
)

export default useStore
