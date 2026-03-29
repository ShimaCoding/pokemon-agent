import { useState, type ReactNode } from 'react'
import styles from './JsonViewer.module.css'

// ── Types ──────────────────────────────────────────────────────────

type JsonPrimitive = string | number | boolean | null
type JsonValue = JsonPrimitive | JsonObject | JsonArray
interface JsonObject { [key: string]: JsonValue }
type JsonArray = JsonValue[]

// ── Primitive renderer ─────────────────────────────────────────────

function Primitive({ value }: { value: JsonPrimitive }) {
  if (value === null)            return <span className={styles.jNull}>null</span>
  if (typeof value === 'boolean') return <span className={styles.jBool}>{String(value)}</span>
  if (typeof value === 'number')  return <span className={styles.jNum}>{value}</span>
  // string: use JSON.stringify to show proper escape sequences
  const repr = JSON.stringify(value)
  const inner = repr.slice(1, -1) // strip outer quotes
  return <span className={styles.jStr}>&quot;{inner}&quot;</span>
}

// ── Recursive node ─────────────────────────────────────────────────

interface NodeProps {
  value: JsonValue
  depth: number
  isLast: boolean
}

function JsonNode({ value, depth, isLast }: NodeProps): ReactNode {
  const [collapsed, setCollapsed] = useState(false)

  const comma = !isLast ? <span className={styles.punct}>,</span> : null

  // ── Primitives (inline) ────────────────────────────────────────
  if (value === null || typeof value !== 'object') {
    return (
      <span>
        <Primitive value={value as JsonPrimitive} />
        {comma}
      </span>
    )
  }

  // ── Containers (object / array) ────────────────────────────────
  const isArray = Array.isArray(value)
  const entries: [string, JsonValue][] = isArray
    ? (value as JsonArray).map((v, i) => [String(i), v as JsonValue])
    : Object.entries(value as JsonObject)

  const open  = isArray ? '[' : '{'
  const close = isArray ? ']' : '}'

  // Empty container
  if (entries.length === 0) {
    return (
      <span>
        <span className={styles.bracket}>{open}{close}</span>
        {comma}
      </span>
    )
  }

  // Collapsed container
  if (collapsed) {
    return (
      <span>
        <span className={styles.bracket}>{open}</span>
        <button
          className={styles.collapseBtn}
          onClick={() => setCollapsed(false)}
          title="Expandir"
        >
          <span className={styles.ellipsis}>▶ {entries.length}</span>
        </button>
        <span className={styles.bracket}>{close}</span>
        {comma}
      </span>
    )
  }

  // Expanded container
  return (
    <>
      <span className={styles.bracket}>{open}</span>
      <button
        className={styles.collapseBtn}
        onClick={() => setCollapsed(true)}
        title="Colapsar"
      >
        ▾
      </button>
      <div className={styles.indent}>
        {entries.map(([k, v], i) => (
          <div key={k} className={styles.entryLine}>
            {!isArray && (
              <>
                <span className={styles.key}>&quot;{k}&quot;</span>
                <span className={styles.colon}>: </span>
              </>
            )}
            <JsonNode
              value={v}
              depth={depth + 1}
              isLast={i === entries.length - 1}
            />
          </div>
        ))}
      </div>
      <span className={styles.bracket}>{close}</span>
      {comma}
    </>
  )
}

// ── Public API ─────────────────────────────────────────────────────

interface JsonViewerProps {
  data: unknown
  maxHeight?: string
}

export function JsonViewer({ data, maxHeight = '140px' }: JsonViewerProps) {
  const [raw, setRaw] = useState(false)

  return (
    <div className={styles.viewer}>
      <div className={styles.toolbar}>
        <button
          className={`${styles.modeBtn} ${!raw ? styles.active : ''}`}
          onClick={() => setRaw(false)}
        >
          pretty
        </button>
        <span className={styles.divider}>|</span>
        <button
          className={`${styles.modeBtn} ${raw ? styles.active : ''}`}
          onClick={() => setRaw(true)}
        >
          raw
        </button>
      </div>
      <div className={styles.content} style={{ maxHeight }}>
        {raw ? (
          <pre className={styles.rawPre}>{JSON.stringify(data, null, 2)}</pre>
        ) : (
          <div className={styles.prettyWrap}>
            <JsonNode value={data as JsonValue} depth={0} isLast />
          </div>
        )}
      </div>
    </div>
  )
}

/** Try to parse a string as JSON. Returns the parsed value or null on failure. */
export function tryParseJson(s: string): unknown | null {
  try {
    return JSON.parse(s)
  } catch {
    return null
  }
}
