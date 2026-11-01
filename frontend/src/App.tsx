import { usePrompts } from './hooks/usePrompts'
import { useMcpCapabilities } from './hooks/useMcpCapabilities'
import ChatView from './components/Chat/ChatView'
import Header from './components/Header/Header'
import SettingsModal from './components/SettingsModal/SettingsModal'

export default function App() {
  usePrompts()
  useMcpCapabilities()
  return (
    <>
      <Header />
      <ChatView />
      <SettingsModal />
    </>
  )
}
