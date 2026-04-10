// app/page.tsx
import { MessageList } from '@/features/chat/components/MessageList'
import { ChatInput } from '@/features/chat/components/ChatInput'

export default function Home() {
  return (
    // 整个屏幕的高度，采用 flex 纵向布局
    <main className="flex h-screen w-full flex-col bg-white dark:bg-gray-900">
      
      {/* 顶部可以留个简单的 Header */}
      <header className="flex h-14 items-center justify-center border-b border-gray-100 dark:border-gray-800">
        <h1 className="text-lg font-semibold">Sky-Chat</h1>
      </header>

      {/* 消息列表占据剩余所有空间 (flex-1) */}
      <MessageList />

      {/* 输入框固定在底部 */}
      <div className="w-full bg-gradient-to-t from-white via-white to-transparent pb-4 pt-2 dark:from-gray-900 dark:via-gray-900">
        <ChatInput />
      </div>
      
    </main>
  )
}