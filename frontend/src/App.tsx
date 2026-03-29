import { useProviders } from './hooks/useProviders'
import { usePrompts } from './hooks/usePrompts'
import Layout from './components/Layout/Layout'

export default function App() {
  useProviders()
  usePrompts()
  return <Layout />
}
