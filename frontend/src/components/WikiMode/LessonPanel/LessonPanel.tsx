import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import styles from './LessonPanel.module.css'
import type { WikiLesson } from '../../../types'

interface Props {
  lesson: WikiLesson | null
  onPrev: () => void
  onNext: () => void
}

export default function LessonPanel({ lesson, onPrev, onNext }: Props) {
  if (!lesson) {
    return (
      <div className={styles.panel}>
        <div className={styles.empty}>
          Clickeá un nodo del diagrama o un evento del timeline para abrir una
          lección. También podés navegar con los botones de abajo.
        </div>
        <div className={styles.footer}>
          <button type="button" className={styles.navBtn} onClick={onPrev}>
            ← Anterior
          </button>
          <button type="button" className={styles.navBtn} onClick={onNext}>
            Siguiente →
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <div className={styles.title}>{lesson.title}</div>
        <div className={styles.levelBadge}>{lesson.level}</div>
      </div>
      <div className={styles.body}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{lesson.body_md}</ReactMarkdown>
      </div>
      <div className={styles.footer}>
        <button type="button" className={styles.navBtn} onClick={onPrev}>
          ← Anterior
        </button>
        <button type="button" className={styles.navBtn} onClick={onNext}>
          Siguiente →
        </button>
      </div>
    </div>
  )
}
