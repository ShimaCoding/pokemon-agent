import { useCallback, useMemo, useState } from 'react'
import type { EventsCatalog, WikiLesson } from '../types'

export interface UseActiveLessonResult {
  activeLessonId: string | null
  setActiveLessonId: (id: string | null) => void
  activeLesson: WikiLesson | null
  navigatePrev: () => void
  navigateNext: () => void
  setFromEvent: (eventType: string) => void
  setFromNode: (nodeId: string) => void
}

export function useActiveLesson(
  lessons: WikiLesson[] | null,
  catalog: EventsCatalog | null,
): UseActiveLessonResult {
  const [activeLessonId, setActiveLessonId] = useState<string | null>(null)

  const activeLesson = useMemo(() => {
    if (!lessons || !activeLessonId) return null
    return lessons.find((l) => l.id === activeLessonId) ?? null
  }, [lessons, activeLessonId])

  const navigateBy = useCallback(
    (delta: number) => {
      if (!lessons || lessons.length === 0) return
      const currentIdx = activeLessonId
        ? lessons.findIndex((l) => l.id === activeLessonId)
        : -1
      const nextIdx =
        currentIdx < 0
          ? 0
          : (currentIdx + delta + lessons.length) % lessons.length
      setActiveLessonId(lessons[nextIdx].id)
    },
    [lessons, activeLessonId],
  )

  const navigatePrev = useCallback(() => navigateBy(-1), [navigateBy])
  const navigateNext = useCallback(() => navigateBy(1), [navigateBy])

  const setFromEvent = useCallback(
    (eventType: string) => {
      if (!catalog) return
      const entry = catalog[eventType]
      if (entry?.related_lesson_id) {
        setActiveLessonId(entry.related_lesson_id)
      }
    },
    [catalog],
  )

  const setFromNode = useCallback(
    (nodeId: string) => {
      // node.related_lesson_id is embedded in the architecture, but the caller
      // will usually pass in the lesson id directly; keep the indirection so
      // callers can pass a node id and we resolve via lessons.related_nodes.
      if (!lessons) return
      const match = lessons.find((l) => l.related_nodes.includes(nodeId))
      if (match) setActiveLessonId(match.id)
    },
    [lessons],
  )

  return {
    activeLessonId,
    setActiveLessonId,
    activeLesson,
    navigatePrev,
    navigateNext,
    setFromEvent,
    setFromNode,
  }
}
