// features/chat/components/ThinkingPanel/index.tsx
'use client'

import { useState, useRef, useEffect } from 'react'
import { ChevronDown, Brain } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useChatStore } from '@/features/chat/store/chat.store'

interface ThinkingPanelProps {
  messageId: string
}

export function ThinkingPanel({ messageId }: ThinkingPanelProps) {
  // 从 Store 获取这条消息的思考内容
  const message = useChatStore((s) => s.messages.find(m => m.id === messageId))
  const isStreaming = useChatStore((s) => s.streamingMessageId === messageId)
  
  const content = message?.thinking || ''
  
  // 只有当有思考内容时才渲染
  const [isExpanded, setIsExpanded] = useState(true)
  const contentRef = useRef<HTMLDivElement>(null)

  // 当正在思考时，如果内容超长，自动滚动到底部
  useEffect(() => {
    if (isStreaming && isExpanded && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [content, isStreaming, isExpanded])

  // 如果这段消息完全没有思考内容，直接不渲染
  if (!content) return null

  // 只要大模型开始输出正文（content有值了），说明思考结束了
  const isThinkingFinished = !!message?.content

  return (
    <div className="mb-4 overflow-hidden rounded-xl border border-gray-200/50 bg-gray-50/50 dark:border-gray-800/50 dark:bg-gray-900/30 transition-all duration-300">
      
      {/* 顶部可点击的折叠栏 */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 hover:bg-gray-100/50 dark:hover:bg-gray-800/50 transition-colors group outline-none"
      >
        <div className="flex items-center gap-2.5">
          <Brain className={`h-4 w-4 ${!isThinkingFinished ? 'text-blue-500 animate-pulse' : 'text-gray-400'}`} />
          <span className="text-sm font-medium text-gray-600 dark:text-gray-300">
            {!isThinkingFinished ? '深度思考中...' : '已完成思考'}
          </span>
        </div>
        <ChevronDown 
          className={`h-4 w-4 text-gray-400 transition-transform duration-300 ${isExpanded ? 'rotate-180' : ''}`} 
        />
      </button>

      {/* 折叠的内容区 */}
      <div 
        className={`transition-all duration-300 ease-in-out ${isExpanded ? 'max-h-[400px] opacity-100' : 'max-h-0 opacity-0'}`}
      >
        <div 
          ref={contentRef}
          className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400 overflow-y-auto max-h-[400px] prose prose-sm dark:prose-invert"
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}