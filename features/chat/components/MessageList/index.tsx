// features/chat/components/MessageList/index.tsx
'use client'

import { useRef, useEffect, useState, useCallback } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useChatStore } from '@/features/chat/store/chat.store'
import { ChatMessage } from '../ChatMessage'

export function MessageList() {
  const messages = useChatStore((s) => s.messages)
  const isSendingMessage = useChatStore((s) => s.isSendingMessage)
  const streamingMessageId = useChatStore((s) => s.streamingMessageId)
  
  // 外层滚动容器
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  
  // 记录用户是否主动往上翻了聊天记录
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false)

  // 1. 初始化虚拟列表钩子 (Virtualizer)
  // 它的作用是：你给我一个总长度，我告诉你当前屏幕应该渲染哪几个序号 (index)
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 100, // 预估每条消息的初始高度，后续会自动测量真实高度
    overscan: 3,             // 在屏幕上下多预留 3 条消息，防止滚动太快白屏
  })

  // 2. 处理自动滚动到底部的逻辑
  // 当有新消息，或者正在流式输出时触发
  useEffect(() => {
    // 如果用户主动往上翻看历史记录了，就别强行把人家拽到底部了！
    if (isUserScrolledUp) return

    // 如果列表里有东西，滚动到最后一个 index
    if (messages.length > 0) {
      virtualizer.scrollToIndex(messages.length - 1, { align: 'end' })
    }
  }, [messages.length, streamingMessageId, virtualizer, isUserScrolledUp])

  // 3. 监听滚动事件，判断用户是不是往上翻了
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current
    if (!el) return
    
    // 如果当前滚动位置 距离 底部 大于 100 像素，就认为用户往上翻了
    const isUp = el.scrollHeight - el.scrollTop - el.clientHeight > 100
    setIsUserScrolledUp(isUp)
  }, [])

  // 4. 空状态展示
  if (messages.length === 0) {
    return (
      <div className="flex h-full flex-1 flex-col items-center justify-center space-y-4">
        <div className="h-16 w-16 rounded-2xl bg-gradient-to-tr from-blue-500 to-purple-500 shadow-lg" />
        <h1 className="text-2xl font-semibold text-gray-800 dark:text-gray-200">
          有什么我可以帮您的？
        </h1>
        <p className="text-sm text-gray-500 max-w-md text-center">
          你可以问我任何问题，比如写代码、查资料、或者让我帮你分析一段数据。
        </p>
      </div>
    )
  }

  // 5. 渲染虚拟列表
  return (
    <div 
      ref={scrollContainerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-4 scroll-smooth custom-scrollbar-auto"
    >
      {/* 虚拟列表的相对定位容器：总高度是计算出来的巨高数字 */}
      <div
        className="relative mx-auto w-full max-w-3xl"
        style={{ height: `${virtualizer.getTotalSize()}px` }}
      >
        {/* 遍历当前屏幕上可见的那些 items */}
        {virtualizer.getVirtualItems().map((virtualItem) => {
          const message = messages[virtualItem.index]
          return (
            <div
              key={virtualItem.key}
              data-index={virtualItem.index}
              ref={virtualizer.measureElement} // 极其关键：告诉引擎这条消息的真实高度！
              className="absolute left-0 top-0 w-full"
              style={{
                transform: `translateY(${virtualItem.start}px)`,
              }}
            >
              <ChatMessage message={message} />
            </div>
          )
        })}
      </div>
    </div>
  )
}