// features/chat/types/chat.ts
//为了让后续代码有极好的代码提示，我们需要定义一套消息类型
export type MessageRole = 'user' | 'assistant' | 'system'

// 单条消息的结构
export interface Message {
  id: string
  role: MessageRole
  content: string
  conversationId?: string
  createdAt?: number
  // 预留给未来多模态扩展的字段
  imageUrls?: string[]
}

// Vercel AI SDK 默认返回的 SSE 块的数据格式
export type SSEEventType = 'text' | 'finish' | 'error' | 'thinking'

export interface SSEData {
  type?: SSEEventType
  textDelta?: string  // 增量的文本内容
  error?: string
  // 某些高级模型可能会有思考过程
  thinkingDelta?: string 
}