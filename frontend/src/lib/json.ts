/** Try to parse a string as JSON. Returns the parsed value or null on failure. */
export function tryParseJson(s: string): unknown | null {
  try {
    return JSON.parse(s)
  } catch {
    return null
  }
}
