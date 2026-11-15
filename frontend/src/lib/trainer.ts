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

/** This browser's trainer id, created on first use. */
export function trainerId(): string {
  if (current) return current
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && TRAINER_ID_RE.test(stored)) {
      current = stored
      return current
    }
    current = crypto.randomUUID()
    localStorage.setItem(STORAGE_KEY, current)
  } catch {
    // Storage blocked (private mode, disabled cookies): the box lasts until the page reloads.
    current ??= crypto.randomUUID()
  }
  return current
}
