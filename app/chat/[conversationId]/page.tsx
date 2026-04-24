// app/chat/[conversationId]/page.tsx
'use client'

import { useEffect, use, useRef } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { MessageList } from '@/features/chat/components/MessageList'
import { ChatInput } from '@/features/chat/components/ChatInput'
import { AuthGuard } from '@/features/auth/components/AuthGuard'
import { MainLayout } from '@/components/MainLayout'
import { ConversationList } from '@/features/conversation/components/ConversationList' 
import { ChatService } from '@/features/chat/services/chat.service'
import { useChatStore } from '@/features/chat/store/chat.store'

export default function ChatPage({ params }: { params: Promise<{ conversationId: string }> }) {
  const resolvedParams = use(params)
  const conversationId = resolvedParams.conversationId
  
  const searchParams = useSearchParams()
  const router = useRouter()
  const clearMessages = useChatStore((s) => s.clearMessages)
  
  // 防止在 React StrictMode 下发送两次
  const hasAutoSentRef = useRef(false)

  useEffect(() => {
    if (conversationId) {
      ChatService.loadMessages(conversationId)
      
      // 【新增逻辑】检查是否有携带的初始消息需要自动发送
      const pendingMsg = searchParams.get('msg')
      if (pendingMsg && !hasAutoSentRef.current) {
        hasAutoSentRef.current = true
        
        // 延迟一丢丢发送，确保 UI 和历史记录（如果有）先加载完毕
        setTimeout(() => {
          ChatService.sendMessage(pendingMsg, conversationId)
          // 发送完后，把 URL 上的参数清理掉，避免刷新页面再次发送
          router.replace(`/chat/${conversationId}`)
        }, 300)
      }
    }
    
    return () => {
      clearMessages()
    }
  }, [conversationId, clearMessages, searchParams, router])

  return (
    <AuthGuard>
      <MainLayout sidebarChildren={<ConversationList />}>
        <header className="flex h-14 items-center justify-center border-b border-gray-100 dark:border-gray-800">
          <h1 className="text-lg font-semibold text-gray-500 text-sm">会话 ID: {conversationId}</h1>
        </header>

        <MessageList />

        <div className="w-full bg-gradient-to-t from-white via-white to-transparent pb-4 pt-2 dark:from-gray-900 dark:via-gray-900">
          <ChatInput conversationId={conversationId} />
        </div>
      </MainLayout>
    </AuthGuard>
  )
}