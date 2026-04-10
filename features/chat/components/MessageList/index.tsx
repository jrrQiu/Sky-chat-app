// features/chat/components/MessageList/index.tsx
// 包裹消息的列表容器
'use client'

import { useRef, useEffect } from 'react'
import { useChatStore } from '@/features/chat/store/chat.store'
import { ChatMessage } from '../ChatMessage'

export function MessageList() {
  const messages = useChatStore((s) => s.messages)
  const isSendingMessage = useChatStore((s) => s.isSendingMessage)
  
  // 用于滚动的锚点
  const scrollAnchorRef = useRef<HTMLDivElement>(null)

  // 当消息列表变化时，自动滚动到底部
  useEffect(() => {
    if (scrollAnchorRef.current) {
      scrollAnchorRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [messages]) // 依赖 messages，每次有新字蹦出来都会触发滚动

  // 空状态提示
  if (messages.length === 0) {
    return (
      <div className="flex h-full flex-1 items-center justify-center">
        <h1 className="text-3xl font-medium text-gray-400">今天想聊点什么？</h1>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 pb-4">
      <div className="mx-auto max-w-3xl">
        {messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        
        {/* 这是一个隐形的锚点，永远在列表最底部 */}
        <div ref={scrollAnchorRef} className="h-px w-full" />
      </div>
    </div>
  )
}