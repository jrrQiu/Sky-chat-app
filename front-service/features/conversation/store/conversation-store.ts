import { create } from 'zustand'
import { apiJson } from '@/lib/api/client'
import type { ConversationData } from '@/features/conversation/types'

interface ConversationApiResult {
  deleted?: boolean
}

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
      const conversations = await apiJson<ConversationData[]>('/v1/conversations')
      set({
        conversations,
        isLoading: false,
        hasInitiallyLoaded: true,
      })
    } catch (error) {
      console.error('[ConversationStore] loadConversations failed:', error)
      set({ isLoading: false, hasInitiallyLoaded: true })
    }
  },

  // 核心：创建新会话
  createConversation: async () => {
    const conversation = await apiJson<ConversationData>('/v1/conversations', {
      method: 'POST',
      body: JSON.stringify({ title: '新对话' }),
    })

    set((state) => ({
      conversations: [conversation, ...state.conversations],
    }))
    return conversation.id
  },

  deleteConversation: async (id) => {
    const prev = get().conversations
    set({ conversations: prev.filter((c) => c.id !== id) })

    try {
      const result = await apiJson<ConversationApiResult>(
        `/v1/conversations/${id}`,
        { method: 'DELETE' }
      )
      if (!result.deleted) {
        set({ conversations: prev })
      }
    } catch (error) {
      set({ conversations: prev })
      console.error(error)
    }
  },

  updateConversationTitle: async (id, title) => {
    const prev = get().conversations
    set({
      conversations: prev.map((c) => (c.id === id ? { ...c, title } : c)),
    })
    try {
      await apiJson(`/v1/conversations/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ title }),
      })
    } catch (error) {
      set({ conversations: prev })
      console.error(error)
    }
  },
}))
