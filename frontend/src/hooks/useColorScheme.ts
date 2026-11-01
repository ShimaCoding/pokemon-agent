import { useSyncExternalStore } from 'react'

const query = window.matchMedia('(prefers-color-scheme: dark)')

function subscribe(onChange: () => void) {
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

export function currentColorScheme(): 'light' | 'dark' {
  return query.matches ? 'dark' : 'light'
}

/** The OS color scheme, kept live; widgets receive it through the host context. */
export function useColorScheme(): 'light' | 'dark' {
  return useSyncExternalStore(subscribe, currentColorScheme)
}
