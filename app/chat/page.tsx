// app/chat/page.tsx
'use client'

import { useEffect, useRef } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useConversationStore } from '@/features/conversation/store/conversation-store'
import { AuthGuard } from '@/features/auth/components/AuthGuard'

function ChatRedirect() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const isCreatingRef = useRef(false)

  useEffect(() => {
    // 使用 ref 防止 React 严格模式下的重复创建
    if (isCreatingRef.current) return
    isCreatingRef.current = true

    const createAndRedirect = async () => {
      try {
        // 调用我们已经写好的 store，在数据库中新建一条会话记录，并拿到它的 ID
        const newId = await useConversationStore.getState().createConversation()
        
        // 看看 URL 里有没有带过来刚才在首页输入的消息参数
        const msg = searchParams.get('msg')
        
        // 带着参数，重定向到真正的会话详情页
        const url = msg ? `/chat/${newId}?msg=${msg}` : `/chat/${newId}`
        router.replace(url)
      } catch (error) {
        console.error('[ChatRedirect] Failed to create conversation:', error)
        isCreatingRef.current = false // 失败时重置，允许重试
        router.push('/')
      }
    }

    createAndRedirect()
  }, [router, searchParams])

  return (
    <div className="flex h-screen items-center justify-center bg-gray-50 dark:bg-gray-900">
      <div className="text-gray-500 animate-pulse">正在为您创建新会话...</div>
    </div>
  )
}

export default function ChatPage() {
  return (
    <AuthGuard>
      <ChatRedirect />
    </AuthGuard>
  )
}