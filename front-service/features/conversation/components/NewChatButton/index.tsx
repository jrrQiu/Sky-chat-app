// features/conversation/components/NewChatButton/index.tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PenSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useConversationStore } from '../../store/conversation-store'
import { useUIStore } from '@/lib/stores/ui.store'

export function NewChatButton() {
  const navigate = useNavigate()
  const createConversation = useConversationStore((s) => s.createConversation)
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const [isCreating, setIsCreating] = useState(false)

  const handleNewChat = async () => {
    if (isCreating) return
    
    setIsCreating(true)
    try {
      const newId = await createConversation()
      navigate(`/chat/${newId}`)
    } catch (error) {
      console.error('新建对话失败:', error)
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <Button
      variant="outline"
      disabled={isCreating}
      onClick={handleNewChat}
      className={`w-full ${collapsed ? 'px-0 justify-center' : 'justify-start gap-3'}`}
    >
      <PenSquare className="h-4 w-4 shrink-0" />
      {!collapsed && <span>{isCreating ? '创建中...' : '新建对话'}</span>}
    </Button>
  )
}
