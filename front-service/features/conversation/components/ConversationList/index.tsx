// features/conversation/components/ConversationList/index.tsx
import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useConversationStore } from '../../store/conversation-store'
import { ConversationItem } from './ConversationItem'

export function ConversationList() {
  const navigate = useNavigate()
  const params = useParams()
  const currentId = params.conversationId as string | undefined

  const { 
    conversations, 
    isLoading, 
    hasInitiallyLoaded, 
    loadConversations,
    deleteConversation,
    updateConversationTitle 
  } = useConversationStore()

  useEffect(() => {
    loadConversations()
  }, [loadConversations])

  // 处理删除逻辑
  const handleDelete = async (id: string) => {
    if (!window.confirm('确定要删除这个会话吗？历史记录将不可恢复。')) return

    // 先算出删除后要去哪：删掉当前会话就把用户扔回落地页（旧行为 navigate('/')）等于
    // 把上下文也一起删了。留在会话区更合理 —— 优先进入相邻的会话，列表空了才回到
    // /chat（它会复用最近会话，没有才新建）。
    const remaining = conversations.filter((conv) => conv.id !== id)
    const wasCurrent = currentId === id

    await deleteConversation(id)

    if (!wasCurrent) return
    const next = remaining[0]
    navigate(next ? `/chat/${next.id}` : '/chat', { replace: true })
  }

  if (isLoading && !hasInitiallyLoaded) {
    return (
      <div className="flex justify-center p-4">
        <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
      </div>
    )
  }

  if (conversations.length === 0) {
    return (
      <div className="text-center text-sm text-gray-500 mt-10">
        暂无历史对话
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1 py-2">
      {conversations.map((conv) => (
        <ConversationItem
          key={conv.id}
          conversation={conv}
          isActive={currentId === conv.id}
          onClick={() => navigate(`/chat/${conv.id}`)}
          onDelete={handleDelete}
          onRename={updateConversationTitle}
        />
      ))}
    </div>
  )
}
