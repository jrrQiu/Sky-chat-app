// features/chat/components/ChatMessage.tsx
'use client'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
// 引入 Github 风格的代码高亮 CSS
import 'highlight.js/styles/github-dark.css' 

import type { Message } from '@/features/chat/types/chat'
import { useChatStore } from '@/features/chat/store/chat.store'
import { createMarkdownComponents } from './MessageContent/MarkdownComponents'

export function ChatMessage({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const streamingMessageId = useChatStore(s => s.streamingMessageId)
  const isStreaming = message.id === streamingMessageId

  // 获取我们刚刚配置好的劫持组件
  const components = createMarkdownComponents()

  return (
    <div className={`flex w-full py-6 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div 
        className={`max-w-[85%] rounded-2xl px-5 py-3.5 text-[15px] leading-relaxed 
          ${isUser 
            ? 'bg-blue-500 text-white' 
            : 'bg-transparent text-gray-900 dark:text-gray-100'
          }
        `}
      >
        <div className={`prose prose-sm max-w-none break-words ${isUser ? 'prose-invert' : 'dark:prose-invert'}`}>
          <ReactMarkdown 
            // 插件 1：支持表格、删除线、自动链接
            remarkPlugins={[remarkGfm]} 
            // 插件 2：支持代码语法高亮
            rehypePlugins={[rehypeHighlight]}
            // 插件 3：使用我们自定义的渲染组件（带复制按钮的 CodeBlock）
            components={components}
          >
            {message.content || '...'}
          </ReactMarkdown>
        </div>

        {/* 流式光标 */}
        {isStreaming && (
          <span className="inline-block h-4 w-1.5 ml-1 animate-pulse bg-black align-middle dark:bg-white" />
        )}
      </div>
    </div>
  )
}