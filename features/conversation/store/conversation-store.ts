// features/conversation/store/conversation-store.ts
import { create } from 'zustand'
import {
  getConversations,
  createConversation as createConversationAction,
  deleteConversation as deleteConversationAction,
  updateConversationTitle as updateTitleAction,
  type ConversationData,
} from '@/app/actions/conversation'

interface ConversationState {
  conversations: ConversationData[]
  isLoading: boolean
  hasInitiallyLoaded: boolean

  // 动作
  loadConversations: () => Promise<void>
  createConversation: () => Promise<string>
  deleteConversation: (id: string) => Promise<void>
  updateConversationTitle: (id: string, title: string) => Promise<void>
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  conversations: [],
  isLoading: false,
  hasInitiallyLoaded: false,

  loadConversations: async () => {
    set({ isLoading: true })
    try {
      const result = await getConversations()
      if (result.success && result.data) {
        set({ conversations: result.data, isLoading: false, hasInitiallyLoaded: true })
      } else {
        set({ isLoading: false, hasInitiallyLoaded: true })
      }
    } catch {
      set({ isLoading: false, hasInitiallyLoaded: true })
    }
  },

  // 核心：创建新会话
  createConversation: async () => {
    const result = await createConversationAction()
    if (result.success && result.data) {
      // 乐观更新：把新会话插到数组最前面
      set((state) => ({
        conversations: [result.data!, ...state.conversations],
      }))
      return result.data.id
    }
    throw new Error(result.error || '创建失败')
  },

  deleteConversation: async (id) => {
    // 乐观更新：先在界面上移除它
    const prev = get().conversations
    set({ conversations: prev.filter((c) => c.id !== id) })
    
    // 发起真实请求
    const result = await deleteConversationAction(id)
    if (!result.success) {
      // 如果失败了，回滚数据
      set({ conversations: prev })
      console.error(result.error)
    }
  },

  updateConversationTitle: async (id, title) => {
    const prev = get().conversations
    set({
      conversations: prev.map((c) => (c.id === id ? { ...c, title } : c)),
    })
    const result = await updateTitleAction(id, title)
    if (!result.success) set({ conversations: prev })
  },
}))