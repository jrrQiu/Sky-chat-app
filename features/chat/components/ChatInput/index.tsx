// features/chat/components/ChatInput/index.tsx
'use client'

import { useState, useRef, useEffect } from 'react'
import TextareaAutosize from 'react-textarea-autosize'
import { SendHorizonal, Square } from 'lucide-react'
import { useChatStore } from '@/features/chat/store/chat.store'
import { ChatService } from '@/features/chat/services/chat.service'
import { Button } from '@/components/ui/button' 

interface ChatInputProps {
  conversationId?: string
}

export function ChatInput({ conversationId }: ChatInputProps) {
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  
  // 从全局 Store 获取状态
  const isSendingMessage = useChatStore((s) => s.isSendingMessage)
  
  // 聚焦输入框
  useEffect(() => {
    if (!isSendingMessage) {
      inputRef.current?.focus()
    }
  }, [isSendingMessage])

  // 处理发送逻辑
  const handleSubmit = () => {
    if (!input.trim() || isSendingMessage) return
    
    // 调用ChatService发送消息方法
    ChatService.sendMessage(input.trim(), conversationId)
    setInput('') // 清空输入框
  }

  // 处理快捷键
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 回车发送，Shift+回车换行
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault() // 阻止默认的回车换行行为
      handleSubmit()
    }
  }

  // 处理停止生成
  const handleStop = () => {
    ChatService.abortStream()
  }

  return (
    <div className="relative mx-auto w-full max-w-3xl p-4">
      <div className="relative flex w-full items-end overflow-hidden rounded-2xl border border-gray-300 bg-white shadow-sm focus-within:ring-1 focus-within:ring-gray-400 dark:border-gray-700 dark:bg-gray-800">
        
        {/* 自动伸缩的文本框 */}
        <TextareaAutosize
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="给 AI 发送消息..."
          className="max-h-[200px] w-full resize-none bg-transparent px-4 py-3 text-sm focus:outline-none"
          minRows={1}
          maxRows={8}
        />

        {/* 右下角的发送/停止按钮 */}
        <div className="p-2">
          {isSendingMessage ? (
            <Button 
              size="icon" 
              variant="destructive" 
              className="h-8 w-8 rounded-xl"
              onClick={handleStop}
              title="停止生成"
            >
              <Square className="h-4 w-4 fill-current" />
            </Button>
          ) : (
            <Button 
              size="icon" 
              className="h-8 w-8 rounded-xl bg-black hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-gray-200"
              onClick={handleSubmit}
              disabled={!input.trim()}
              title="发送"
            >
              <SendHorizonal className="h-4 w-4" />
            </Button>
          )}
        </div>
        
      </div>
      <div className="mt-2 text-center text-xs text-gray-500">
        内容由 AI 生成，请仔细甄别。
      </div>
    </div>
  )
}