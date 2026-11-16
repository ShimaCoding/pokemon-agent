// Who owns the box Bill keeps (Caja de Bill): an id this browser generates once
// and sends with every message. It is the only credential of the box, so it
// outlives conversations: "Nueva" starts another conversation, never another
// trainer. Clearing the site data, or another browser profile, is another
// trainer with an empty box.
//
// A plain localStorage read/write, not a zustand store: `persist` only writes
// on `set`, so a store holding just this initial value would never be saved.

const STORAGE_KEY = 'mcpokedex-trainer'
// Same pattern the backend accepts (backend/sessions.py::TRAINER_ID_RE).
const TRAINER_ID_RE = /^[a-z0-9-]{32,64}$/

let current: string | null = null

/**
 * A random UUID. `crypto.randomUUID` only exists on secure origins (https, localhost) and
 * recent browsers; `getRandomValues` works everywhere, so the id is built from it otherwise.
 */
function randomId(): string | null {
  try {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80 // RFC 4122 variant
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  } catch {
    return null
  }
}

/**
 * This browser's trainer id, created on first use. Undefined when no id can be made at
 * all: the message is sent without one and the chat works, only the box is unavailable
 * (the backend treats `trainer_id` as optional). Never throws.
 */
export function trainerId(): string | undefined {
  if (current) return current
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && TRAINER_ID_RE.test(stored)) {
      current = stored
      return current
    }
  } catch {
    /* storage blocked (private mode, disabled cookies) */
  }
  current = randomId()
  if (!current) return undefined
  try {
    localStorage.setItem(STORAGE_KEY, current)
  } catch {
    // Not saved: the box lasts until the page reloads.
  }
  return current
}
