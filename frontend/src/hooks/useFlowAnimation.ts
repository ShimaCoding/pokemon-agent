import { useEffect, useRef, useState } from 'react'
import type { TraceEvent } from '../types'
import type { FlowNodeState } from '../components/WikiMode/FlowDiagram/FlowNode'

/**
 * Derives a per-node visual state map from the stream of SSE events.
 *
 * State machine:
 * - `idle`    : default, no recent activity
 * - `active`  : the node is currently being exercised (pulses)
 * - `success` : brief green flash; transient states auto-decay back to idle
 * - `error`   : red flash + shake; decays back to idle
 *
 * "Brief" states (success/error flashes) auto-revert via setTimeout after
 * BRIEF_MS unless a newer event comes in.
 */

const NODE_IDS = ['browser', 'fastapi', 'strands_agent', 'llm_router', 'mcp_server'] as const
export type NodeId = (typeof NODE_IDS)[number]

export type NodeStates = Record<string, FlowNodeState>

const BRIEF_MS = 800

type Interest = { from: string; to: string } | null

export interface UseFlowAnimationResult {
  nodeStates: NodeStates
  /** Pair (from→to) currently being traversed, for edge highlighting. */
  activeEdge: Interest
}

function initialStates(): NodeStates {
  return NODE_IDS.reduce<NodeStates>((acc, id) => {
    acc[id] = 'idle'
    return acc
  }, {})
}

export function useFlowAnimation(
  traceLogs: TraceEvent[],
  agentResponseLength: number = 0,
): UseFlowAnimationResult {
  const [nodeStates, setNodeStates] = useState<NodeStates>(() => initialStates())
  const [activeEdge, setActiveEdge] = useState<Interest>(null)
  const processedUpTo = useRef(0)
  const timers = useRef<Map<string, number>>(new Map())

  // Reset when the trace log is cleared / restarts (length went down).
  useEffect(() => {
    if (traceLogs.length < processedUpTo.current) {
      processedUpTo.current = 0
      for (const t of timers.current.values()) window.clearTimeout(t)
      timers.current.clear()
      setNodeStates(initialStates())
      setActiveEdge(null)
    }
  }, [traceLogs.length])

  useEffect(() => {
    if (traceLogs.length === processedUpTo.current) return
    const newEvents = traceLogs.slice(processedUpTo.current)
    processedUpTo.current = traceLogs.length

    const scheduleRevert = (nodeId: string, to: FlowNodeState = 'idle') => {
      // Clear previous timer for this node
      const prev = timers.current.get(nodeId)
      if (prev) window.clearTimeout(prev)
      const handle = window.setTimeout(() => {
        setNodeStates((s) => {
          // Only revert if not already overwritten
          if (s[nodeId] !== 'success' && s[nodeId] !== 'error') return s
          return { ...s, [nodeId]: to }
        })
        timers.current.delete(nodeId)
      }, BRIEF_MS)
      timers.current.set(nodeId, handle)
    }

    for (const ev of newEvents) {
      // Treat as a loose tagged union — we also handle synthesized event
      // types (e.g. system_log) that don't carry per-node state.
      const type = (ev as { type: string }).type
      switch (type) {
        case 'system_log':
          // First system_log ("Provider: …") implicitly marks FastAPI as active.
          setNodeStates((s) =>
            s.fastapi === 'idle' ? { ...s, fastapi: 'active' } : s,
          )
          break
        case 'agent_init':
          setNodeStates((s) => ({ ...s, strands_agent: 'active', mcp_server: 'success' }))
          setActiveEdge({ from: 'strands_agent', to: 'mcp_server' })
          scheduleRevert('mcp_server')
          break
        case 'llm_call':
          setNodeStates((s) => ({ ...s, llm_router: 'active', strands_agent: 'active' }))
          setActiveEdge({ from: 'strands_agent', to: 'llm_router' })
          break
        case 'model_attempt': {
          const ok = (ev as { status?: string }).status === 'success'
          setNodeStates((s) => ({ ...s, llm_router: ok ? 'success' : 'error' }))
          scheduleRevert('llm_router')
          break
        }
        case 'tool_call':
          setNodeStates((s) => ({ ...s, mcp_server: 'active', strands_agent: 'active' }))
          setActiveEdge({ from: 'strands_agent', to: 'mcp_server' })
          break
        case 'tool_result':
          setNodeStates((s) => ({ ...s, mcp_server: 'success' }))
          scheduleRevert('mcp_server')
          break
        case 'done':
          // Clear any pending timers so success state persists for all nodes.
          for (const t of timers.current.values()) window.clearTimeout(t)
          timers.current.clear()
          setNodeStates(() => ({
            browser: 'success',
            fastapi: 'success',
            strands_agent: 'success',
            llm_router: 'success',
            mcp_server: 'success',
          }))
          setActiveEdge(null)
          break
        case 'error':
          // Mark the node(s) currently active as errored.
          setNodeStates((s) => {
            const next = { ...s }
            let marked = false
            for (const id of NODE_IDS) {
              if (s[id] === 'active') {
                next[id] = 'error'
                marked = true
              }
            }
            if (!marked) next.fastapi = 'error'
            return next
          })
          setActiveEdge(null)
          break
        default:
          break
      }
    }
  }, [traceLogs])

  // React to streaming text deltas: once the assistant response starts
  // growing, light up Browser + FastAPI and the edge between them.
  const lastResponseLen = useRef(0)
  useEffect(() => {
    if (agentResponseLength > lastResponseLen.current && agentResponseLength > 0) {
      setNodeStates((s) => {
        if (s.browser === 'success' && s.fastapi === 'success') return s
        return { ...s, browser: 'active', fastapi: 'active' }
      })
      setActiveEdge({ from: 'fastapi', to: 'browser' })
    }
    lastResponseLen.current = agentResponseLength
  }, [agentResponseLength])

  // Cleanup timers on unmount.
  useEffect(() => {
    return () => {
      for (const t of timers.current.values()) window.clearTimeout(t)
      timers.current.clear()
    }
  }, [])

  return { nodeStates, activeEdge }
}
