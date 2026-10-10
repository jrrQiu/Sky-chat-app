import { useEffect } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { MainLayout } from '@/components/MainLayout'
import { ConversationList } from '@/features/conversation/components/ConversationList'
import { ChatInput } from '@/features/chat/components/ChatInput'
import { MessageList } from '@/features/chat/components/MessageList'
import { ChatService } from '@/features/chat/services/chat.service'
import { useChatStore } from '@/features/chat/store/chat.store'

/**
 * 落地页把首条消息通过 `?msg=` 传进来，这里做**一次性消费**。
 *
 * 存放在模块级而不是组件 ref，是因为旧写法有两处会出错：
 *
 * 1. **重复发送。** 旧代码在 300ms 后才把 `?msg=` 从 URL 上摘掉，而 URL 是刷新后唯一
 *    存活的东西：在摘掉之前刷新一次（或任何重新挂载），同一条消息就会被再发一遍。
 *    重复发送意味着**新的 runId**，而服务端幂等是按 runId 去重的，挡不住它 ——
 *    如果这句话会建工单，就是第二张工单。
 * 2. **消息丢失。** 旧代码用组件内 ref 做重入保护，而这个项目的 `main.tsx` 开了
 *    `<StrictMode>`：开发模式下 effect 会「挂载 → 清理 → 再挂载」，第一次清理就把
 *    定时器取消了，第二次挂载又因为 ref 已置位而直接跳过 —— 结果是消息永远发不出去，
 *    而 `?msg=` 一直挂在 URL 上，等某次重新挂载时才意外发出。
 *
 * 所以：参数在发送**之前**就摘掉（刷新不可重放），消息本体暂存模块级变量（清理不丢）。
 */
let pendingAutoSend: { conversationId: string; message: string } | null = null

/** 同步取走待发消息：取走即清空，保证 StrictMode 的两次执行只有一次能拿到。 */
function claimPendingAutoSend(conversationId: string): string | null {
  if (pendingAutoSend?.conversationId !== conversationId) return null
  const message = pendingAutoSend.message
  pendingAutoSend = null
  return message
}

export function ChatConversationPage() {
  const { conversationId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const clearMessages = useChatStore((state) => state.clearMessages)

  useEffect(() => {
    if (!conversationId) return

    const fromUrl = searchParams.get('msg')
    if (fromUrl) {
      pendingAutoSend = { conversationId, message: fromUrl }
      navigate(`/chat/${conversationId}`, { replace: true })
    }

    const autoSend = claimPendingAutoSend(conversationId)

    // 历史先落地、再发送，避免乐观插入的用户消息被随后返回的历史覆盖。
    // 发送挂在 effect 之外（不 await、不在清理里取消）：StrictMode 会取消 effect 的
    // 清理回调和定时器，但取消不了一个已经启动的 promise。
    void (async () => {
      try {
        await ChatService.loadMessages(conversationId)
      } catch (error) {
        console.error('[ChatConversation] loadMessages failed:', error)
      }
      if (!autoSend) return
      try {
        await ChatService.sendMessage(autoSend, conversationId)
      } catch (error) {
        console.error('[ChatConversation] auto-send failed:', error)
      }
    })()

    return () => clearMessages()
  }, [clearMessages, conversationId, navigate, searchParams])

  return (
    <MainLayout sidebarChildren={<ConversationList />}>
      <header className="flex h-14 items-center justify-center border-b border-gray-100 dark:border-gray-800">
        <h1 className="text-sm font-semibold text-gray-500">
          会话 ID: {conversationId}
        </h1>
      </header>

      <MessageList />

      <div className="w-full bg-gradient-to-t from-white via-white to-transparent pb-4 pt-2 dark:from-gray-900 dark:via-gray-900">
        <ChatInput conversationId={conversationId} />
      </div>
    </MainLayout>
  )
}
