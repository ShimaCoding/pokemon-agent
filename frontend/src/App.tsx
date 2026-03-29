import { useProviders } from './hooks/useProviders'
import { usePrompts } from './hooks/usePrompts'
import { useMcpCapabilities } from './hooks/useMcpCapabilities'
import Layout from './components/Layout/Layout'

export default function App() {
  useProviders()
  usePrompts()
  useMcpCapabilities()
  return <Layout />
}
