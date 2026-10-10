import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useConversationStore } from '@/features/conversation/store/conversation-store'
import type { ConversationData } from '@/features/conversation/types'

/**
 * 最近**更新**的那条会话。
 *
 * 不能用 `conversations[0]`：服务端是按「置顶优先，其次 updated_at 倒序」返回的
 * （ConversationMapper 的 ORDER BY），所以置顶过的旧会话会排在最前面。而进入 `/chat`
 * 的语义是"接着上次聊"，置顶的用途是"方便找"，不是"默认打开"。
 */
function pickMostRecent(conversations: ConversationData[]): ConversationData | undefined {
  return conversations.reduce<ConversationData | undefined>((best, item) => {
    if (!best) return item
    const itemTime = Date.parse(item.updatedAt ?? '') || 0
    const bestTime = Date.parse(best.updatedAt ?? '') || 0
    return itemTime >= bestTime ? item : best
  }, undefined)
}

export function ChatRedirectPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const isCreatingRef = useRef(false)

  useEffect(() => {
    if (isCreatingRef.current) return
    isCreatingRef.current = true

    const createAndRedirect = async () => {
      const message = searchParams.get('msg')
      try {
        const store = useConversationStore.getState()

        // `/chat` 的语义是「进入聊天」，不是「新建会话」。
        //
        // 这里以前无条件 `createConversation()`，于是每一次经过这个路由都会在列表里
        // 多出一个空对话：登录成功后（登录回调就是跳到 /chat）、在 /chat 上刷新、
        // 用浏览器的前进/后退回到 /chat —— 全部触发。用户看到的正是「每次登录都新建
        // 一个对话」。显式新建是侧边栏「新建对话」按钮的职责（它直接 create 后跳
        // /chat/{id}，不经过这里），所以这里应该打开最近的会话。
        //
        // 只有**一条会话都没有**时才新建：否则打开一个空对话页对用户没有任何意义。
        if (!store.hasInitiallyLoaded) {
          await store.loadConversations()
        }

        const latest = pickMostRecent(useConversationStore.getState().conversations)
        const targetId =
          latest?.id ?? (await useConversationStore.getState().createConversation())

        navigate(
          message ? `/chat/${targetId}?msg=${encodeURIComponent(message)}` : `/chat/${targetId}`,
          { replace: true }
        )
      } catch (error) {
        console.error('[ChatRedirect] Failed to resolve a conversation:', error)
        isCreatingRef.current = false
        navigate('/', { replace: true })
      }
    }

    void createAndRedirect()
  }, [navigate, searchParams])

  return (
    <div className="flex h-screen items-center justify-center bg-gray-50 dark:bg-gray-900">
      <div className="animate-pulse text-gray-500">正在为您创建新会话...</div>
    </div>
  )
}
