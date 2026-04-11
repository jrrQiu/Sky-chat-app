// app/chat/[conversationId]/page.tsx
import { MessageList } from '@/features/chat/components/MessageList'
import { ChatInput } from '@/features/chat/components/ChatInput'
import { AuthGuard } from '@/features/auth/components/AuthGuard'
import { MainLayout } from '@/components/MainLayout'

export default function ChatPage({ params }: { params: { conversationId: string } }) {
  // 从 URL 参数中拿到当前的历史会话 ID
  const { conversationId } = params

  return (
    <AuthGuard>
      <MainLayout 
        sidebarChildren={
          <div className="text-sm text-gray-500 text-center mt-10">
            暂无历史会话
          </div>
        }
      >
        <header className="flex h-14 items-center justify-center border-b border-gray-100 dark:border-gray-800">
          <h1 className="text-lg font-semibold">历史会话：{conversationId}</h1>
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