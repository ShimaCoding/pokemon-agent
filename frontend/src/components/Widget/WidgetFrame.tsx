import { useEffect, useRef, useState } from 'react'
import { AppBridge, PostMessageTransport } from '@modelcontextprotocol/ext-apps/app-bridge'
import type { McpUiHostContext } from '@modelcontextprotocol/ext-apps/app-bridge'
import { callServerTool, fetchUiResource } from '../../lib/api'
import { sendChatMessage } from '../../hooks/useAgentStream'
import type { UiResourceCsp, UiResourceEvent } from '../../types'
import styles from './WidgetFrame.module.css'

const HOST_INFO = { name: 'MCPokédex', version: '1.0.0' }
const TEARDOWN_TIMEOUT_MS = 500

/** Only https origins (no wildcards, paths or quotes) may reach the CSP. */
function safeOrigins(list: string[] | undefined): string[] {
  return (list ?? []).filter((o) => /^https:\/\/[a-z0-9.-]+(:\d+)?$/i.test(o))
}

/**
 * Build the widget's Content-Security-Policy from its `_meta.ui.csp`.
 * Everything not declared is denied: inline script/style only, images and
 * fonts from the declared resource domains, network from connect domains.
 */
function buildCsp(csp: UiResourceCsp): string {
  const resources = safeOrigins(csp.resourceDomains).join(' ')
  const connect = safeOrigins(csp.connectDomains).join(' ') || "'none'"
  const frames = safeOrigins(csp.frameDomains).join(' ') || "'none'"
  return [
    "default-src 'none'",
    `script-src 'unsafe-inline' ${resources}`.trim(),
    `style-src 'unsafe-inline' ${resources}`.trim(),
    `img-src data: blob: ${resources}`.trim(),
    `font-src data: ${resources}`.trim(),
    `media-src data: ${resources}`.trim(),
    `connect-src ${connect}`,
    `frame-src ${frames}`,
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ')
}

function withCsp(html: string, csp: UiResourceCsp): string {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${buildCsp(csp)}">`
  // The policy must come before any script in the document to apply to it.
  return /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => `${m}${meta}`) : meta + html
}

interface Props {
  event: UiResourceEvent
}

/**
 * MCP Apps host for one widget: a sandboxed iframe (scripts only — no
 * same-origin, forms, popups or top navigation) talking to the page through
 * AppBridge over postMessage. Widget requests are served by the backend.
 */
export default function WidgetFrame({ event }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(0)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    const iframe = iframeRef.current
    if (!iframe?.contentWindow) return
    let disposed = false

    const hostContext: McpUiHostContext = {
      // The GBC look has a single (light) theme; widgets follow it.
      theme: 'light',
      displayMode: 'inline',
      availableDisplayModes: ['inline'],
      platform: 'web',
      locale: navigator.language || 'es',
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      toolInfo: { tool: { name: event.tool, inputSchema: { type: 'object' } } },
    }

    const bridge = new AppBridge(
      null,
      HOST_INFO,
      { openLinks: {}, serverTools: {}, logging: {}, message: { text: {} } },
      { hostContext },
    )

    bridge.oninitialized = () => {
      if (disposed) return
      setState('ready')
      void bridge.sendToolInput({ arguments: event.tool_input })
      void bridge.sendToolResult(event.tool_result as Parameters<AppBridge['sendToolResult']>[0])
    }
    bridge.onsizechange = ({ height: h }) => {
      if (typeof h === 'number' && h > 0) setHeight(Math.ceil(h))
    }
    bridge.oncalltool = async (params) =>
      (await callServerTool(params.name, params.arguments ?? {})) as Awaited<
        ReturnType<NonNullable<AppBridge['oncalltool']>>
      >
    bridge.onopenlink = async ({ url }) => {
      if (!/^https:\/\//i.test(url)) return { isError: true }
      window.open(url, '_blank', 'noopener,noreferrer')
      return {}
    }
    bridge.onmessage = async ({ content }) => {
      const text = content
        .map((block) => (block.type === 'text' ? block.text : ''))
        .join('\n')
        .trim()
      if (!text) return { isError: true }
      void sendChatMessage(text)
      return {}
    }

    // Connect before loading the document so the widget's `ui/initialize` can't be missed.
    const transport = new PostMessageTransport(iframe.contentWindow, iframe.contentWindow)
    void (async () => {
      try {
        await bridge.connect(transport)
        const resource = await fetchUiResource(event.resource_uri)
        if (disposed) return
        iframe.srcdoc = withCsp(resource.html, resource.csp ?? {})
      } catch (err) {
        if (disposed) return
        setErrorMsg(err instanceof Error ? err.message : 'No se pudo cargar el widget')
        setState('error')
      }
    })()

    return () => {
      disposed = true
      // Give the widget a moment to clean up, then drop the channel either way.
      const timeout = new Promise((resolve) => setTimeout(resolve, TEARDOWN_TIMEOUT_MS))
      void Promise.race([bridge.teardownResource({}).catch(() => undefined), timeout]).finally(() => {
        void bridge.close()
      })
    }
  }, [event])

  return (
    <figure className={styles.widget} data-state={state}>
      {state === 'loading' && <div className={styles.skeleton} aria-hidden="true" />}
      {state === 'error' && (
        <figcaption className={styles.error}>No se pudo mostrar el widget: {errorMsg}</figcaption>
      )}
      <iframe
        ref={iframeRef}
        className={styles.frame}
        title={`Widget de ${event.tool}`}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        style={{ height: state === 'ready' && height ? height : 0 }}
      />
    </figure>
  )
}
