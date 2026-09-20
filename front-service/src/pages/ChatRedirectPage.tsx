import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useConversationStore } from '@/features/conversation/store/conversation-store'

export function ChatRedirectPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const isCreatingRef = useRef(false)

  useEffect(() => {
    if (isCreatingRef.current) return
    isCreatingRef.current = true

    const createAndRedirect = async () => {
      try {
        const newId = await useConversationStore.getState().createConversation()
        const message = searchParams.get('msg')
        navigate(
          message ? `/chat/${newId}?msg=${encodeURIComponent(message)}` : `/chat/${newId}`,
          { replace: true }
        )
      } catch (error) {
        console.error('[ChatRedirect] Failed to create conversation:', error)
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
