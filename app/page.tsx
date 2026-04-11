// app/page.tsx
import { MessageList } from '@/features/chat/components/MessageList'
import { ChatInput } from '@/features/chat/components/ChatInput'
import { AuthGuard } from '@/features/auth/components/AuthGuard' // 新增

export default function Home() {
  return (
    // 用 AuthGuard 包裹核心页面！
    <AuthGuard>
      <main className="flex h-screen w-full flex-col bg-white dark:bg-gray-900">
        <header className="flex h-14 items-center justify-center border-b border-gray-100 dark:border-gray-800">
          <h1 className="text-lg font-semibold">Sky-Chat</h1>
        </header>

        <MessageList />

        <div className="w-full bg-gradient-to-t from-white via-white to-transparent pb-4 pt-2 dark:from-gray-900 dark:via-gray-900">
          <ChatInput />
        </div>
      </main>
    </AuthGuard>
  )
}