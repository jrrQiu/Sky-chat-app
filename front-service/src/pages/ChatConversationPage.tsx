import { useEffect, useRef } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { MainLayout } from '@/components/MainLayout'
import { ConversationList } from '@/features/conversation/components/ConversationList'
import { ChatInput } from '@/features/chat/components/ChatInput'
import { MessageList } from '@/features/chat/components/MessageList'
import { ChatService } from '@/features/chat/services/chat.service'
import { useChatStore } from '@/features/chat/store/chat.store'

export function ChatConversationPage() {
  const { conversationId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const clearMessages = useChatStore((state) => state.clearMessages)
  const hasAutoSentRef = useRef(false)

  useEffect(() => {
    if (!conversationId) return

    void ChatService.loadMessages(conversationId)

    const pendingMessage = searchParams.get('msg')
    if (pendingMessage && !hasAutoSentRef.current) {
      hasAutoSentRef.current = true
      const timer = window.setTimeout(() => {
        void ChatService.sendMessage(pendingMessage, conversationId)
        navigate(`/chat/${conversationId}`, { replace: true })
      }, 300)

      return () => {
        window.clearTimeout(timer)
        clearMessages()
      }
    }

    return () => clearMessages()
  }, [clearMessages, conversationId, navigate, searchParams])

  return (
    <MainLayout sidebarChildren={<ConversationList />}>
      <header className="flex h-14 items-center justify-center border-b border-gray-100 dark:border-gray-800">
        <h1 className="text-sm font-semibold text-gray-500">
          会话 ID: {conversationId}
        </h1>
      </header>

      <MessageList />

      <div className="w-full bg-gradient-to-t from-white via-white to-transparent pb-4 pt-2 dark:from-gray-900 dark:via-gray-900">
        <ChatInput conversationId={conversationId} />
      </div>
    </MainLayout>
  )
}
