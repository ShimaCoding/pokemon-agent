import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { McpResource, McpTool, PokemonData, Provider, SlashPrompt, TabKey, TraceEvent } from '../types'

// SECURITY (Finding 5): cap trace log history to avoid unbounded memory growth.
const MAX_TRACE_LOGS = 200

// ── Types ────────────────────────────────────────────────────────

interface AppState {
  // API Key (persisted)
  apiKey: string
  setApiKey: (key: string) => void
  clearApiKey: () => void

  // Dev mode toggle (persisted): true=consola, false=pokedex/dexter
  devMode: boolean
  setDevMode: (v: boolean) => void

  // Fast-Forward (bypass UI animation)
  fastForward: boolean
  setFastForward: (v: boolean) => void

  // Settings modal
  settingsOpen: boolean
  setSettingsOpen: (open: boolean) => void
  settingsDismissed: boolean
  setSettingsDismissed: (dismissed: boolean) => void

  // Intro modal
  introOpen: boolean
  setIntroOpen: (open: boolean) => void
  introDismissed: boolean
  setIntroDismissed: (dismissed: boolean) => void

  // Query draft (pre-fill input from intro modal examples)
  queryDraft: string
  queryDraftAutoSubmit: boolean
  setQueryDraft: (q: string, autoSubmit?: boolean) => void
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
  animatedAgentResponse: string
  appendText: (delta: string) => void
  setAnimatedAgentResponse: (text: string) => void

  // Trace logs
  traceLogs: TraceEvent[]
  visibleTraceCount: number
  appendTraceLog: (event: TraceEvent) => void
  setVisibleTraceCount: (count: number | ((c: number) => number)) => void

  // In-flight status (for disabling UI inputs)
  inFlight: boolean
  setInFlight: (v: boolean) => void

  // Rate-limit countdown (seconds remaining, 0 = no limit)
  rateLimitSeconds: number
  setRateLimitSeconds: (n: number) => void

  // Session reset (between queries)
  resetSession: () => void

  // Pre-query mobile state
  preQuery: boolean
  setPreQuery: (v: boolean) => void
}

// ── Store ────────────────────────────────────────────────────────

// SECURITY (Finding 1): API key is persisted in sessionStorage (cleared on tab
// close) instead of localStorage, reducing the window of exposure to XSS and
// browser-extension theft. For full protection, migrate to httpOnly cookies.

// One-time migration: remove the old localStorage entry left from before the
// switch to sessionStorage so stale API keys don't linger in the browser.
localStorage.removeItem('mcpokedex-storage')

const useStore = create<AppState>()(
  persist(
    (set) => ({
      // API Key
      apiKey: '',
      setApiKey: (key) => set({ apiKey: key }),
      clearApiKey: () => set({ apiKey: '' }),

      // Dev mode
      devMode: true,
      setDevMode: (v) => set({ devMode: v, fastForward: !v }),

      // Fast-Forward
      fastForward: false,
      setFastForward: (v) => set({ fastForward: v }),

      // Settings modal
      settingsOpen: false,
      setSettingsOpen: (open) => set({ settingsOpen: open }),
      settingsDismissed: false,
      setSettingsDismissed: (dismissed) => set({ settingsDismissed: dismissed }),

      // Intro modal
      introOpen: false,
      setIntroOpen: (open) => set({ introOpen: open }),
      introDismissed: false,
      setIntroDismissed: (dismissed) => set({ introDismissed: dismissed }),

      // Query draft
      queryDraft: '',
      queryDraftAutoSubmit: false,
      setQueryDraft: (q, autoSubmit = false) => set({ queryDraft: q, queryDraftAutoSubmit: autoSubmit }),
      clearQueryDraft: () => set({ queryDraft: '', queryDraftAutoSubmit: false }),

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
      animatedAgentResponse: '',
      appendText: (delta) =>
        set((s) => ({ agentResponse: s.agentResponse + delta })),
      setAnimatedAgentResponse: (text: string) => set({ animatedAgentResponse: text }),

      // Trace logs
      traceLogs: [],
      visibleTraceCount: 0,
      appendTraceLog: (event) =>
        set((s) => {
          const updated = [...s.traceLogs, event]
          return { traceLogs: updated.length > MAX_TRACE_LOGS ? updated.slice(-MAX_TRACE_LOGS) : updated }
        }),
      setVisibleTraceCount: (count) => 
        set((s) => ({ visibleTraceCount: typeof count === 'function' ? count(s.visibleTraceCount) : count })),

      // In-flight
      inFlight: false,
      setInFlight: (v) => set({ inFlight: v }),

      // Rate-limit countdown
      rateLimitSeconds: 0,
      setRateLimitSeconds: (n) => set({ rateLimitSeconds: n }),

      // Session reset (tab navigation handled by useAgentStream based on devMode)
      resetSession: () =>
        set({ agentResponse: '', animatedAgentResponse: '', traceLogs: [], visibleTraceCount: 0 }),

      // Pre-query (mobile)
      preQuery: true,
      setPreQuery: (v) => set({ preQuery: v }),
    }),
    {
      name: 'mcpokedex-storage',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({ apiKey: state.apiKey, introDismissed: state.introDismissed, settingsDismissed: state.settingsDismissed, devMode: state.devMode, fastForward: state.fastForward }),
    }
  )
)

export default useStore
