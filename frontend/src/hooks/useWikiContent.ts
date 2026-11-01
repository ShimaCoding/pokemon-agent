import { useEffect, useState } from 'react'
import type { EventsCatalog, WikiLesson } from '../types'

// Module-level cache: single fetch per page load, shared by every trace panel.
interface WikiBundle {
  lessons: WikiLesson[]
  catalog: EventsCatalog
}

let cache: WikiBundle | null = null
let pending: Promise<WikiBundle> | null = null

function fetchBundle(): Promise<WikiBundle> {
  if (cache) return Promise.resolve(cache)
  pending ??= (async () => {
    const [lessonsRes, catalogRes] = await Promise.all([
      fetch('/api/wiki/lessons'),
      fetch('/api/wiki/events-catalog'),
    ])
    if (!lessonsRes.ok || !catalogRes.ok) throw new Error('Error cargando lecciones')
    cache = {
      lessons: (await lessonsRes.json()) as WikiLesson[],
      catalog: (await catalogRes.json()) as EventsCatalog,
    }
    return cache
  })().finally(() => {
    pending = null
  })
  return pending
}

/** The lesson that explains a given SSE event type, once loaded (null otherwise). */
export function useLessonFor(eventType: string): WikiLesson | null {
  const [bundle, setBundle] = useState<WikiBundle | null>(cache)

  useEffect(() => {
    if (bundle) return
    let cancelled = false
    fetchBundle()
      .then((b) => !cancelled && setBundle(b))
      .catch(() => {
        /* lessons are optional */
      })
    return () => {
      cancelled = true
    }
  }, [bundle])

  if (!bundle) return null
  const lessonId = bundle.catalog[eventType]?.related_lesson_id
  return bundle.lessons.find((l) => l.id === lessonId) ?? null
}
