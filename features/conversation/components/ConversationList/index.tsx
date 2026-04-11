// features/conversation/components/ConversationList/index.tsx
'use client'

import { useEffect } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Loader2 } from 'lucide-react'
import { useConversationStore } from '../../store/conversation-store'
import { ConversationItem } from './ConversationItem'

export function ConversationList() {
  const router = useRouter()
  const params = useParams()
  // 从 URL 里拿到当前选中的会话 ID
  const currentId = params.conversationId as string | undefined

  const { 
    conversations, 
    isLoading, 
    hasInitiallyLoaded, 
    loadConversations,
    deleteConversation,
    updateConversationTitle 
  } = useConversationStore()

  // 组件挂载时拉数据
  useEffect(() => {
    loadConversations()
  }, [loadConversations])

  // 处理删除逻辑
  const handleDelete = async (id: string) => {
    // 弹个原生确认框（你可以换成更优雅的 Dialog）
    if (!window.confirm('确定要删除这个会话吗？历史记录将不可恢复。')) return
    
    await deleteConversation(id)
    
    // 如果删除的是当前正在看的会话，我们要把路由跳回首页
    if (currentId === id) {
      router.push('/')
    }
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
          onClick={() => router.push(`/chat/${conv.id}`)}
          onDelete={handleDelete}
          onRename={updateConversationTitle}
        />
      ))}
    </div>
  )
}