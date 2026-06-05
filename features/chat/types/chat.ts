/**
 * Sky Chat 核心类型定义
 * 
 * 本文件定义了聊天系统的核心数据结构，包括：
 * - 消息类型和角色定义
 * - 流式传输（SSE）相关类型
 * - 工具调用和函数定义
 * - 聊天配置和状态管理
 */

export type MessageRole = 'user' | 'assistant' | 'system'

export type SSEEventType = 'thinking' | 'answer' | 'tool_calls' | 'tool_call' | 'tool_progress' | 'tool_result' | 'complete'

export interface ToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export interface FunctionDef {
  name: string
  description: string
  parameters: Record<string, unknown>
}

export interface Tool {
  type: 'function'
  function: FunctionDef
}

export interface SearchSource {
  title: string
  url: string
  snippet?: string
}

export interface ToolResult {
  toolCallId: string
  name: string
  result: {
    success: boolean
    imageUrl?: string
    prompt?: string
    resultCount?: number
    sources?: SearchSource[]
    [key: string]: unknown
  }
}

export type ToolInvocationState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface ToolInvocation {
  toolCallId: string
  name: string
  state: ToolInvocationState
  args?: {
    query?: string
    prompt?: string
    [key: string]: unknown
  }
  result?: {
    success: boolean
    imageUrl?: string
    resultCount?: number
    sources?: SearchSource[]
    [key: string]: unknown
  }
}

export type MessageDisplayState =
  | 'idle'
  | 'streaming'
  | 'waiting'
  | 'error'
  | 'regenerating'

export interface FileAttachment {
  name: string
  type: 'txt' | 'md'
  size: number
  content: string
}

export interface Message {
  id: string
  role: MessageRole
  content: string
  thinking?: string
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
  toolInvocations?: ToolInvocation[]
  hasError?: boolean
  sessionId?: string
  userMessage?: string
  isStreaming?: boolean
  timestamp?: number
  attachments?: FileAttachment[]
  displayState?: MessageDisplayState
  conversationId?: string
  createdAt?: number
  imageUrls?: string[]
}

export interface ChatConfig {
  model?: string
  enableThinking?: boolean
  thinkingBudget?: number
  tools?: Tool[]
  enableTTS?: boolean
  ttsVoice?: string
  ttsSpeed?: number
}

export interface SSEData {
  type: SSEEventType
  content?: string
  tool_calls?: ToolCall[]
  sessionId?: string
  progress?: number
  toolCallId?: string
  name?: string
  query?: string
  prompt?: string
  resultCount?: number
  success?: boolean
  imageUrl?: string
  width?: number
  height?: number
  sources?: SearchSource[]
  estimatedTime?: number
  cancelled?: boolean
}

export type AbortReason = 'user_stop' | 'user_retry' | 'tab_hidden' | 'network_error'

export type StreamingPhase = 'thinking' | 'answer' | null

export interface PipelineState {
  messages: Message[]
  isLoading: boolean
  isGeneratingAudio: boolean
  isPlayingAudio: boolean
}