// features/chat/components/ChatMessage.tsx
'use client' 
//单条消息组件
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '@/features/chat/types/chat'
import { useChatStore } from '@/features/chat/store/chat.store'

export function ChatMessage({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const streamingMessageId = useChatStore(s => s.streamingMessageId)
  
  // 判断当前这条消息是不是正在被 AI 流式输出
  const isStreaming = message.id === streamingMessageId

  return (
    <div className={`flex w-full py-6 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div 
        className={`max-w-[85%] rounded-2xl px-5 py-3.5 text-[15px] leading-relaxed 
          ${isUser 
            ? 'bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100' 
            : 'bg-transparent text-gray-900 dark:text-gray-100'
          }
        `}
      >
        {/* Markdown 渲染引擎 */}
        <div className="prose prose-sm max-w-none dark:prose-invert break-words">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content || '...'}
          </ReactMarkdown>
        </div>

        {/* 正在流式输出时，尾部闪烁的黑色光标 */}
        {isStreaming && (
          <span className="inline-block h-4 w-1.5 ml-1 animate-pulse bg-black align-middle dark:bg-white" />
        )}
      </div>
    </div>
  )
}