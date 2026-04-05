import { useEffect, useState } from 'react'
import type { Architecture, EventsCatalog, WikiLesson } from '../types'

// Module-level cache: single fetch per session.
interface WikiBundle {
  lessons: WikiLesson[]
  architecture: Architecture
  catalog: EventsCatalog
}

let _cache: WikiBundle | null = null
let _pending: Promise<WikiBundle> | null = null

async function fetchBundle(): Promise<WikiBundle> {
  if (_cache) return _cache
  if (_pending) return _pending
  _pending = (async () => {
    const [lessonsRes, archRes, catalogRes] = await Promise.all([
      fetch('/api/wiki/lessons'),
      fetch('/api/wiki/architecture'),
      fetch('/api/wiki/events-catalog'),
    ])
    if (!lessonsRes.ok || !archRes.ok || !catalogRes.ok) {
      throw new Error('Error cargando contenido wiki')
    }
    const bundle: WikiBundle = {
      lessons: (await lessonsRes.json()) as WikiLesson[],
      architecture: (await archRes.json()) as Architecture,
      catalog: (await catalogRes.json()) as EventsCatalog,
    }
    _cache = bundle
    return bundle
  })()
  try {
    return await _pending
  } finally {
    _pending = null
  }
}

export interface UseWikiContentResult {
  lessons: WikiLesson[] | null
  architecture: Architecture | null
  catalog: EventsCatalog | null
  loading: boolean
  error: string | null
}

export function useWikiContent(): UseWikiContentResult {
  const [state, setState] = useState<UseWikiContentResult>(() => ({
    lessons: _cache?.lessons ?? null,
    architecture: _cache?.architecture ?? null,
    catalog: _cache?.catalog ?? null,
    loading: _cache === null,
    error: null,
  }))

  useEffect(() => {
    if (_cache) return
    let cancelled = false
    setState((s) => ({ ...s, loading: true, error: null }))
    fetchBundle()
      .then((bundle) => {
        if (cancelled) return
        setState({
          lessons: bundle.lessons,
          architecture: bundle.architecture,
          catalog: bundle.catalog,
          loading: false,
          error: null,
        })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const msg = err instanceof Error ? err.message : 'Error desconocido'
        setState({ lessons: null, architecture: null, catalog: null, loading: false, error: msg })
      })
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
