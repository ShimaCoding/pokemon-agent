import { usePrompts } from './hooks/usePrompts'
import { useMcpCapabilities } from './hooks/useMcpCapabilities'
import ChatView from './components/Chat/ChatView'
import Header from './components/Header/Header'
import SettingsModal from './components/SettingsModal/SettingsModal'
import UndoToast from './components/Chat/UndoToast'

export default function App() {
  usePrompts()
  useMcpCapabilities()
  return (
    <>
      <Header />
      <ChatView />
      <SettingsModal />
      <UndoToast />
    </>
  )
}
