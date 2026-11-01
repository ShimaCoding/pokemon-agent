import { useEffect } from 'react'
import { useSettings } from '../store/useStore'
import type { McpResource, McpTool } from '../types'

export function useMcpCapabilities() {
  const apiKey          = useSettings((s) => s.apiKey)
  const setMcpTools     = useSettings((s) => s.setMcpTools)
  const setMcpResources = useSettings((s) => s.setMcpResources)
  const setMcpLoaded    = useSettings((s) => s.setMcpLoaded)

  useEffect(() => {
    const headers: Record<string, string> = {}
    if (apiKey) headers['X-API-Key'] = apiKey

    async function load() {
      const [toolsResp, resourcesResp] = await Promise.allSettled([
        fetch('/api/tools', { headers }).then((r) => (r.ok ? r.json() : [])),
        fetch('/api/resources', { headers }).then((r) => (r.ok ? r.json() : [])),
      ])

      if (toolsResp.status === 'fulfilled') {
        setMcpTools((toolsResp.value as McpTool[]) ?? [])
      }
      if (resourcesResp.status === 'fulfilled') {
        setMcpResources((resourcesResp.value as McpResource[]) ?? [])
      }
      setMcpLoaded(true)
    }

    void load()
  }, [apiKey, setMcpTools, setMcpResources, setMcpLoaded])
}
