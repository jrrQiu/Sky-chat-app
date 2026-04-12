// features/chat/components/ChatInput/index.tsx
'use client'

import { useState, useRef, useEffect } from 'react'
import TextareaAutosize from 'react-textarea-autosize'
import { SendHorizonal, Square, Globe } from 'lucide-react' // 引入了 Globe 图标做联网搜索演示
import { useChatStore } from '@/features/chat/store/chat.store'
import { ChatService } from '@/features/chat/services/chat.service'
import { Button } from '@/components/ui/button'
import { ModelSelector } from '../ModelSelector'

interface ChatInputProps {
  conversationId?: string
}

export function ChatInput({ conversationId }: ChatInputProps) {
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  
  // 从 Store 取出模型状态和修改方法
  const isSendingMessage = useChatStore((s) => s.isSendingMessage)
  const selectedModel = useChatStore((s) => s.selectedModel)
  const setModel = useChatStore((s) => s.setModel)
  
  // 本地演示：联网搜索开关状态
  const [enableWebSearch, setEnableWebSearch] = useState(false)

  useEffect(() => {
    if (!isSendingMessage) inputRef.current?.focus()
  }, [isSendingMessage])

  const handleSubmit = () => {
    if (!input.trim() || isSendingMessage) return
    ChatService.sendMessage(input.trim(), conversationId)
    setInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="relative mx-auto w-full max-w-3xl p-4">
      {/* 顶部工具栏：模型选择器 + 功能开关 */}
      <div className="mb-2 flex items-center justify-between px-1">
        <ModelSelector value={selectedModel} onChange={setModel} />
        
        {/* 演示：联网搜索开关 */}
        <button 
          onClick={() => setEnableWebSearch(!enableWebSearch)}
          className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            enableWebSearch 
              ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400' 
              : 'text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'
          }`}
        >
          <Globe className="h-3.5 w-3.5" />
          联网搜索
        </button>
      </div>

      {/* 输入框主体 */}
      <div className="relative flex w-full items-end overflow-hidden rounded-2xl border border-gray-300 bg-white shadow-sm focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:focus-within:border-blue-500">
        <TextareaAutosize
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="给 AI 发送消息..."
          className="max-h-[200px] w-full resize-none bg-transparent px-4 py-3.5 text-sm focus:outline-none"
          minRows={1}
          maxRows={8}
        />

        <div className="p-2">
          {isSendingMessage ? (
            <Button size="icon" variant="destructive" className="h-8 w-8 rounded-xl" onClick={() => ChatService.abortStream()}>
              <Square className="h-4 w-4 fill-current" />
            </Button>
          ) : (
            <Button 
              size="icon" 
              className="h-8 w-8 rounded-xl bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-200 dark:disabled:bg-gray-700"
              onClick={handleSubmit}
              disabled={!input.trim()}
            >
              <SendHorizonal className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      <div className="mt-2 text-center text-xs text-gray-400">
        AI 可能会犯错。请核实重要信息。
      </div>
    </div>
  )
}