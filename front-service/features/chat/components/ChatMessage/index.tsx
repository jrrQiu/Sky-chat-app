/**
 * Chat Message Module - 消息模块
 * 
 * Container Component（容器组件）
 * 连接 Store，使用消息状态机
 * 
 * @module modules/chat-message
 */

import { useParams } from 'react-router-dom'
import { useChatStore } from '@/features/chat/store/chat.store'
import { selectMessagePhase, selectIsProcessing } from '@/features/chat/store/selectors'
import { ChatService } from '@/features/chat/services/chat.service'
import { ChatMessageUI } from './ChatMessageUI'

interface ChatMessageProps {
  messageId: string
}

export function ChatMessage({ messageId }: ChatMessageProps) {
  const params = useParams()
  const conversationId = params.conversationId as string

  // Store 数据
  const message = useChatStore((s) => s.messages.find((m) => m.id === messageId))
  const messages = useChatStore((s) => s.messages)
  const isSendingMessage = useChatStore((s) => s.isSendingMessage)
  
  // 状态机数据
  const phase = useChatStore(selectMessagePhase(messageId))
  const isProcessing = useChatStore(selectIsProcessing(messageId))

  if (!message) return null

  // 位置判断
  const isLastMessage = messages[messages.length - 1]?.id === messageId
  const isAIMessage = message.role === 'assistant'
  const isWaitingForResponse = isSendingMessage && isLastMessage && isAIMessage
  
  const handleRetry = isAIMessage 
    ? () => {
        void ChatService.retryMessage(conversationId, messageId)
      }
    : undefined
  
  const handleResume = isAIMessage && message.runId && message.displayState === 'interrupted'
    ? () => {
        void ChatService.resumeMessage(conversationId, messageId)
      }
    : undefined

  const handleCancelTool = isAIMessage
    ? (toolCallId: string) => {
        void ChatService.cancelTool(messageId, toolCallId)
      }
    : undefined

  return (
    <ChatMessageUI
      message={message}
      messageId={messageId}
      phase={phase}
      isProcessing={isProcessing}
      isWaitingForResponse={isWaitingForResponse}
      onRetry={handleRetry}
      onResume={handleResume}
      onCancelTool={handleCancelTool}
    />
  )
}
