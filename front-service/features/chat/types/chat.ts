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

export type SSEEventType =
  | 'thinking'
  | 'answer'
  | 'conversation_title'
  | 'tool_calls'
  | 'tool_call'
  | 'tool_progress'
  | 'tool_result'
  | 'error'
  | 'complete'
  /**
   * 上下文引擎的装配报告（Context Engine v2）。
   *
   * 服务端每个回答都会带一条：用了多少预算、丢弃了哪些消息与证据、引用编号
   * 映射。它同时被 Java 侧落库（滚动摘要）与诊断使用，**不是**给用户看的正文，
   * 因此界面忽略它；保留在类型里是为了让"未知事件"与"已知但不在界面展示"
   * 区分开——前者要能在评审时被看见。
   */
  | 'context_report'
  /**
   * 工具确定性失败后要求用户补充信息（槽位回填）。
   *
   * 服务端把"编号不存在 / 参数不合法 / 权限不足"这类**用户能修**的失败，从"硬失败"改成
   * "提问"：事件里带问题与缺失字段，用户的下一条消息就是回填，不需要重放本轮。
   * 依赖故障（服务不可用）不会走这个事件——让用户对着故障重打一遍字没有意义。
   */
  | 'clarification_required'

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

export type ToolInvocationState =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

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
  | 'interrupted'
  | 'regenerating'

export interface FileAttachment {
  name: string
  type: 'txt' | 'md'
  size: number
  content: string
}

/**
 * 一次"需要用户补充信息"的请求。
 *
 * `missingFields` 可能是工具名（无法更精确时）或字段点路径，界面据此显示中文标签；
 * 未知字段名原样显示，避免悄悄丢掉服务端给出的线索。
 */
export interface Clarification {
  question: string
  missingFields: string[]
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
  /** 服务端的错误码（如 `TURN_TIMEOUT`、`RESULT_UNKNOWN`），用于选择提示文案与操作。 */
  errorCode?: string
  /** 服务端给的错误说明。之前界面只显示固定的"生成失败"，把这段细节丢掉了。 */
  errorMessage?: string
  /** 本轮要求补充信息时存在；用户的下一条消息即为回填。 */
  clarification?: Clarification
  sessionId?: string
  userMessage?: string
  isStreaming?: boolean
  timestamp?: number
  attachments?: FileAttachment[]
  displayState?: MessageDisplayState
  conversationId?: string
  createdAt?: number
  imageUrls?: string[]
  runId?: string
  lastSeq?: number
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
  seq?: number
  runId?: string
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
  message?: string
  args?: Record<string, unknown>
  step?: boolean
  /** Payload of a `context_report` event; server-side diagnostics, not UI content. */
  report?: Record<string, unknown>
  /** `clarification_required`: the question to put to the user. */
  question?: string
  /** `clarification_required`: which fields the question is about. */
  missingFields?: string[]
  /** `error`: a machine-readable reason (`TURN_TIMEOUT`, `RESULT_UNKNOWN`, ...). */
  errorCode?: string
}

export type AbortReason =
  | 'user_stop'
  | 'user_retry'
  | 'tab_hidden'
  | 'network_error'

export type StreamingPhase = 'thinking' | 'answer' | null

export interface PipelineState {
  messages: Message[]
  isLoading: boolean
  isGeneratingAudio: boolean
  isPlayingAudio: boolean
}
