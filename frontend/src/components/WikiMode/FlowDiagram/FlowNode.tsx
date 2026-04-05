import styles from './FlowDiagram.module.css'
import type { ArchitectureNode } from '../../../types'

export type FlowNodeState = 'idle' | 'active' | 'success' | 'error'

interface Props {
  node: ArchitectureNode
  state?: FlowNodeState
  onClick?: (nodeId: string) => void
}

const NODE_W = 130
const NODE_H = 44

export default function FlowNode({ node, state = 'idle', onClick }: Props) {
  const x = node.position.x
  const y = node.position.y
  const stateClass =
    state === 'active'
      ? styles.stateActive
      : state === 'success'
        ? styles.stateSuccess
        : state === 'error'
          ? styles.stateError
          : styles.stateIdle

  return (
    <g
      onClick={onClick ? () => onClick(node.id) : undefined}
      role={onClick ? 'button' : undefined}
      className={`${styles.nodeGroup} ${stateClass}`}
    >
      <title>{node.description}</title>
      <rect
        x={x}
        y={y}
        width={NODE_W}
        height={NODE_H}
        rx={3}
        className={styles.nodeRect}
      />
      <text x={x + NODE_W / 2} y={y + NODE_H / 2 + 4} className={styles.nodeLabel}>
        {node.label}
      </text>
    </g>
  )
}

export const NODE_SIZE = { width: NODE_W, height: NODE_H }
