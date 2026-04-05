import { useMemo } from 'react'
import styles from './FlowDiagram.module.css'
import FlowNode, { NODE_SIZE } from './FlowNode'
import type { FlowNodeState } from './FlowNode'
import type { Architecture } from '../../../types'

interface Props {
  architecture: Architecture
  onNodeClick?: (nodeId: string) => void
  nodeStates?: Record<string, FlowNodeState>
  activeEdge?: { from: string; to: string } | null
}

// Viewbox padding around extreme node positions.
const PAD = 20

export default function FlowDiagram({
  architecture,
  onNodeClick,
  nodeStates,
  activeEdge,
}: Props) {
  const { nodes, edges } = architecture

  const nodeById = useMemo(
    () => Object.fromEntries(nodes.map((n) => [n.id, n])),
    [nodes],
  )

  const viewBox = useMemo(() => {
    const xs = nodes.map((n) => n.position.x)
    const ys = nodes.map((n) => n.position.y)
    const minX = Math.min(...xs) - PAD
    const minY = Math.min(...ys) - PAD
    const maxX = Math.max(...xs) + NODE_SIZE.width + PAD
    const maxY = Math.max(...ys) + NODE_SIZE.height + PAD
    return `${minX} ${minY} ${maxX - minX} ${maxY - minY}`
  }, [nodes])

  return (
    <div className={styles.diagramWrap}>
      <svg
        className={styles.svg}
        viewBox={viewBox}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Diagrama de arquitectura del agente"
      >
        <defs>
          <marker
            id="wiki-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--gbc-mid, #585858)" />
          </marker>
        </defs>

        {edges.map((edge, idx) => {
          const from = nodeById[edge.from]
          const to = nodeById[edge.to]
          if (!from || !to) return null
          const x1 = from.position.x + NODE_SIZE.width / 2
          const y1 = from.position.y + NODE_SIZE.height / 2
          const x2 = to.position.x + NODE_SIZE.width / 2
          const y2 = to.position.y + NODE_SIZE.height / 2
          const midX = (x1 + x2) / 2
          const midY = (y1 + y2) / 2 - 4
          const isActive =
            !!activeEdge && activeEdge.from === edge.from && activeEdge.to === edge.to
          return (
            <g key={`${edge.from}-${edge.to}-${idx}`}>
              <line
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                className={`${styles.edge} ${isActive ? styles.edgeActive : ''}`}
                markerEnd="url(#wiki-arrow)"
              />
              <text x={midX} y={midY} className={styles.edgeLabel} textAnchor="middle">
                {edge.label}
              </text>
            </g>
          )
        })}

        {nodes.map((node) => (
          <FlowNode
            key={node.id}
            node={node}
            state={nodeStates?.[node.id] ?? 'idle'}
            onClick={onNodeClick}
          />
        ))}
      </svg>
    </div>
  )
}
