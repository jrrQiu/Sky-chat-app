// features/chat/store/chat.store.ts
// 用于管理聊天状态和操作的 Store
import { create } from 'zustand'
import type { Message } from '../types/chat'

// 定义 Store 里存了哪些数据（状态）
interface ChatState {
  // 当前会话的所有消息列表
  messages: Message[]
  
  // 是否正在请求大模型
  isSendingMessage: boolean
  
  // 当前正在“打字机”流式生成的 AI 消息的 ID
  // 只有当这个 ID 存在时，UI 才会显示闪烁的光标
  streamingMessageId: string | null
  
  // 选中的大模型
  selectedModel: string
}

// 定义可以怎么修改这些数据（动作）
interface ChatActions {
  // 全量覆盖消息列表（比如切换会话时）
  setMessages: (messages: Message[]) => void
  
  // 新增一条消息
  addMessage: (message: Message) => void
  
  // 更新某条特定消息的内容（这是流式渲染的核心）
  updateMessage: (id: string, updates: Partial<Message>) => void
  
  // 流式传输专用：往某条消息的内容末尾不断拼接新字
  appendContent: (id: string, chunk: string) => void
  
  // 清空当前聊天框
  clearMessages: () => void
  
  // 设置发送状态
  setSendingMessage: (sending: boolean) => void
  
  // 标记哪条消息正在流式生成
  startStreaming: (messageId: string) => void
  
  // 结束流式生成
  stopStreaming: () => void
  
  // 切换模型
  setModel: (modelId: string) => void
}

// 结合 State 和 Actions，创建最终的 Store
export const useChatStore = create<ChatState & ChatActions>((set) => ({
  // === 初始状态 ===
  messages: [],
  isSendingMessage: false,
  streamingMessageId: null,
  selectedModel: 'deepseek-ai/DeepSeek-V3',

  // === 动作实现 ===
  
  setMessages: (messages) => set({ messages }),
  
  addMessage: (message) => 
    set((state) => ({ messages: [...state.messages, message] })),
    
  updateMessage: (id, updates) =>
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.id === id ? { ...msg, ...updates } : msg
      ),
    })),
    
  // 找到正在生成的 AI 消息，并把新的 chunk 拼接到旧的 content 后面
  appendContent: (id, chunk) =>
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.id === id ? { ...msg, content: msg.content + chunk } : msg
      ),
    })),

  clearMessages: () => set({ messages: [] }),
  
  setSendingMessage: (sending) => set({ isSendingMessage: sending }),
  
  startStreaming: (messageId) => set({ streamingMessageId: messageId }),
  
  stopStreaming: () => set({ streamingMessageId: null }),
  
  setModel: (modelId) => set({ selectedModel: modelId }),
}))