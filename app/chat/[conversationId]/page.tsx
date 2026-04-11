// app/chat/[conversationId]/page.tsx

import { useEffect } from 'react'
import { MessageList } from '@/features/chat/components/MessageList'
import { ChatInput } from '@/features/chat/components/ChatInput'
import { AuthGuard } from '@/features/auth/components/AuthGuard'
import { MainLayout } from '@/components/MainLayout'
import { ConversationList } from '@/features/conversation/components/ConversationList' 
import { ChatService } from '@/features/chat/services/chat.service' // 引入服务
import { useChatStore } from '@/features/chat/store/chat.store'     // 引入状态

export default function ChatPage({ params }: { params: { conversationId: string } }) {
  // 从 URL 参数中拿到当前的历史会话 ID
  const { conversationId } = params
  // 引入清空消息的方法
  const clearMessages = useChatStore((s) => s.clearMessages)

  // 当 URL 里的 conversationId 发生变化时，立刻拉取新的历史记录
  useEffect(() => {
    if (conversationId) {
      ChatService.loadMessages(conversationId)
    }
    
    // 卸载组件时（比如切回首页新对话），清空当前聊天框，防止数据串台
    return () => {
      clearMessages()
    }
  }, [conversationId, clearMessages])
  return (
    <AuthGuard>
      <MainLayout sidebarChildren={<ConversationList />}>
        <header className="flex h-14 items-center justify-center border-b border-gray-100 dark:border-gray-800">
          <h1 className="text-lg font-semibold text-gray-500 text-sm">会话 ID: {conversationId}</h1>
        </header>

        <MessageList />

        <div className="w-full bg-gradient-to-t from-white via-white to-transparent pb-4 pt-2 dark:from-gray-900 dark:via-gray-900">
          {/* 把 conversationId 传给输入框，这样发消息时后端就知道属于哪个会话了！ */}
          <ChatInput conversationId={conversationId} />
        </div>
      </MainLayout>
    </AuthGuard>
  )
}