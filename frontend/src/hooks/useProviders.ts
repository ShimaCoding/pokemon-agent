import { useEffect } from 'react'
import useStore from '../store/useStore'

export function useProviders() {
  const setProviders = useStore((s) => s.setProviders)
  const setSelectedProvider = useStore((s) => s.setSelectedProvider)

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

        const groq = avail.find(
          (p) => String(p.name).toLowerCase() === 'groq'
        )
        if (groq) {
          setSelectedProvider(groq.name)
        } else if (avail.length > 0) {
          setSelectedProvider(avail[0].name)
        }
      } catch {
        setProviders([])
      }
    }
    void load()
  }, [setProviders, setSelectedProvider])
}
