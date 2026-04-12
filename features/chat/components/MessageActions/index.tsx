// features/chat/components/MessageActions/index.tsx
'use client'

import { useState } from 'react'
import { Copy, Check, RotateCw, Volume2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useChatStore } from '@/features/chat/store/chat.store'
import { ChatService } from '@/features/chat/services/chat.service'

interface MessageActionsProps {
  messageId: string
  content: string
  role: 'user' | 'assistant'
}

export function MessageActions({ messageId, content, role }: MessageActionsProps) {
  const [isCopied, setIsCopied] = useState(false)
  const isSendingMessage = useChatStore(s => s.isSendingMessage)

  // 1. 复制到剪贴板
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setIsCopied(true)
      setTimeout(() => setIsCopied(false), 2000)
    } catch (err) {
      console.error('复制失败:', err)
    }
  }

  // 2. 重新生成（仅限 AI 消息）
  const handleRegenerate = () => {
    if (isSendingMessage) return
    
    const store = useChatStore.getState()
    const conversationId = store.messages[0]?.conversationId
    
    // 找到当前这条 AI 消息对应的“上一条用户消息”
    const currentIndex = store.messages.findIndex(m => m.id === messageId)
    const prevUserMessage = store.messages.slice(0, currentIndex).reverse().find(m => m.role === 'user')
    
    if (prevUserMessage) {
      // 简单粗暴的重新生成逻辑：切断当前流，重新发送上一条消息
      ChatService.sendMessage(prevUserMessage.content, conversationId)
    }
  }

  // 3. 语音朗读
  const handlePlayAudio = () => {
    alert('正在开发中：这里将接入真实的 Text-to-Speech 语音合成！')
  }

  return (
    <div className={`flex items-center gap-1 mt-2 opacity-0 transition-opacity duration-200 group-hover:opacity-100 ${role === 'user' ? 'justify-end' : 'justify-start'}`}>
      
      {/* 朗读按钮 */}
      {role === 'assistant' && (
        <Button 
          variant="ghost" 
          size="icon" 
          className="h-7 w-7 text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
          onClick={handlePlayAudio}
          title="朗读"
        >
          <Volume2 className="h-3.5 w-3.5" />
        </Button>
      )}

      {/* 复制按钮 */}
      <Button 
        variant="ghost" 
        size="icon" 
        className="h-7 w-7 text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
        onClick={handleCopy}
        title="复制内容"
      >
        {isCopied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
      </Button>

      {/* 重新生成按钮 */}
      {role === 'assistant' && !isSendingMessage && (
        <Button 
          variant="ghost" 
          size="icon" 
          className="h-7 w-7 text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
          onClick={handleRegenerate}
          title="重新生成"
        >
          <RotateCw className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  )
}