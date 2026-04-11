// features/conversation/store/conversation-store.ts
// 会话状态管理
// 用于存储和管理当前登录用户的所有历史会话
import { create } from 'zustand'
import { getConversations, type ConversationData } from '@/app/actions/conversation'

interface ConversationState {
  conversations: ConversationData[]
  isLoading: boolean
  hasInitiallyLoaded: boolean
  
  // 动作
  loadConversations: () => Promise<void>
}

export const useConversationStore = create<ConversationState>((set) => ({
  conversations: [],
  isLoading: false,
  hasInitiallyLoaded: false,

  loadConversations: async () => {
    set({ isLoading: true })
    try {
      // 直接调用 Server Action
      const result = await getConversations()
      if (result.success && result.data) {
        set({
          conversations: result.data,
          isLoading: false,
          hasInitiallyLoaded: true,
        })
      } else {
        set({ isLoading: false, hasInitiallyLoaded: true })
      }
    } catch (error) {
      set({ isLoading: false, hasInitiallyLoaded: true })
    }
  },
}))