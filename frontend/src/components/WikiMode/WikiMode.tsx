import { useEffect, useState } from 'react'
import useStore from '../../store/useStore'
import { useWikiContent } from '../../hooks/useWikiContent'
import { useActiveLesson } from '../../hooks/useActiveLesson'
import { useFlowAnimation } from '../../hooks/useFlowAnimation'
import DexterConsole from '../DexterConsole/DexterConsole'
import FlowDiagram from './FlowDiagram/FlowDiagram'
import Timeline from './Timeline/Timeline'
import LessonPanel from './LessonPanel/LessonPanel'
import styles from './WikiMode.module.css'

type WikiTab = 'chat' | 'diagram' | 'lesson'

export default function WikiMode() {
  const { lessons, architecture, catalog, loading, error } = useWikiContent()
  const lessonCtrl = useActiveLesson(lessons, catalog)
  const traceLogs = useStore((s) => s.traceLogs)
  const agentResponseLength = useStore((s) => s.agentResponse.length)
  const { nodeStates, activeEdge } = useFlowAnimation(traceLogs, agentResponseLength)

  const [mobileTab, setMobileTab] = useState<WikiTab>('diagram')

  // Auto-activate lesson based on the most recent SSE event type
  useEffect(() => {
    if (!catalog || traceLogs.length === 0) return
    const last = traceLogs[traceLogs.length - 1]
    if (!last?.type) return
    const entry = catalog[last.type]
    if (entry?.related_lesson_id && !lessonCtrl.activeLessonId) {
      lessonCtrl.setActiveLessonId(entry.related_lesson_id)
    }
  }, [traceLogs, catalog, lessonCtrl])

  if (loading) {
    return (
      <div className={styles.wikiMode}>
        <div className={styles.loading}>Cargando wiki…</div>
      </div>
    )
  }
  if (error || !architecture || !lessons || !catalog) {
    return (
      <div className={styles.wikiMode}>
        <div className={styles.error}>Error: {error ?? 'contenido no disponible'}</div>
      </div>
    )
  }

  const handleNodeClick = (nodeId: string) => {
    const node = architecture.nodes.find((n) => n.id === nodeId)
    if (node?.related_lesson_id) {
      lessonCtrl.setActiveLessonId(node.related_lesson_id)
    } else {
      lessonCtrl.setFromNode(nodeId)
    }
    setMobileTab('lesson')
  }

  const handleEventClick = (eventType: string) => {
    lessonCtrl.setFromEvent(eventType)
    setMobileTab('lesson')
  }

  return (
    <div className={styles.wikiMode}>
      <div className={styles.tabNav}>
        <button
          className={`${styles.tabBtn} ${mobileTab === 'chat' ? styles.tabActive : ''}`}
          onClick={() => setMobileTab('chat')}
        >
          CHAT
        </button>
        <button
          className={`${styles.tabBtn} ${mobileTab === 'diagram' ? styles.tabActive : ''}`}
          onClick={() => setMobileTab('diagram')}
        >
          FLUJO
        </button>
        <button
          className={`${styles.tabBtn} ${mobileTab === 'lesson' ? styles.tabActive : ''}`}
          onClick={() => setMobileTab('lesson')}
        >
          LECCIÓN
        </button>
      </div>

      <section className={`${styles.column} ${mobileTab === 'chat' ? styles.columnVisible : ''}`}>
        <div className={styles.columnHeader}>Chat</div>
        <div className={styles.columnBody}>
          <DexterConsole />
        </div>
      </section>

      <section
        className={`${styles.column} ${mobileTab === 'diagram' ? styles.columnVisible : ''}`}
      >
        <div className={styles.columnHeader}>Arquitectura & Timeline</div>
        <div className={styles.centerBody}>
          <div className={styles.centerDiagram}>
            <FlowDiagram
              architecture={architecture}
              onNodeClick={handleNodeClick}
              nodeStates={nodeStates}
              activeEdge={activeEdge}
            />
          </div>
          <div className={styles.centerTimeline}>
            <Timeline
              traceLogs={traceLogs}
              catalog={catalog}
              onEventClick={handleEventClick}
            />
          </div>
        </div>
      </section>

      <section className={`${styles.column} ${mobileTab === 'lesson' ? styles.columnVisible : ''}`}>
        <div className={styles.columnHeader}>Lección</div>
        <div className={styles.columnBody}>
          <LessonPanel
            lesson={lessonCtrl.activeLesson}
            onPrev={lessonCtrl.navigatePrev}
            onNext={lessonCtrl.navigateNext}
          />
        </div>
      </section>
    </div>
  )
}
