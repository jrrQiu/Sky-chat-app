// features/conversation/components/ConversationList/index.tsx
'use client'

import { useEffect } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { MessageSquare, Loader2 } from 'lucide-react'
import { useConversationStore } from '../../store/conversation-store'

export function ConversationList() {
  const router = useRouter()
  const params = useParams()
  // 从 URL 里拿到当前选中的会话 ID
  const currentId = params.conversationId as string | undefined

  const { conversations, isLoading, hasInitiallyLoaded, loadConversations } = useConversationStore()

  // 组件挂载时，去服务端拉数据
  useEffect(() => {
    loadConversations()
  }, [loadConversations])

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
      {conversations.map((conv) => {
        const isActive = currentId === conv.id
        return (
          <button
            key={conv.id}
            onClick={() => router.push(`/chat/${conv.id}`)}
            className={`
              flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors text-left
              ${isActive 
                ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' 
                : 'text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
              }
            `}
          >
            <MessageSquare className="h-4 w-4 shrink-0 opacity-70" />
            <span className="truncate flex-1 font-medium">{conv.title}</span>
          </button>
        )
      })}
    </div>
  )
}