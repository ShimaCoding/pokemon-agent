import { useEffect } from 'react'
import useStore from '../store/useStore'

export function useProviders() {
  const setProviders = useStore((s) => s.setProviders)
  const setSelectedProvider = useStore((s) => s.setSelectedProvider)
  const apiKey = useStore((s) => s.apiKey)

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch('/providers')
        const list = (await res.json()) as Array<{
          name: string
          label: string
          available: boolean
        }>
        const avail = list.filter((p) => p.available)
        setProviders(avail)

        // With API key → default to Groq (full model list unlocked).
        // Without API key → default to first available (OpenRouter free tier).
        if (apiKey) {
          const groq = avail.find((p) => String(p.name).toLowerCase() === 'groq')
          if (groq) {
            setSelectedProvider(groq.name)
            return
          }
        }
        if (avail.length > 0) {
          setSelectedProvider(avail[0].name)
        }
      } catch {
        setProviders([])
      }
    }
    void load()
  // Re-run when apiKey changes so the default resets if the user adds/removes their key.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setProviders, setSelectedProvider, apiKey])
}
