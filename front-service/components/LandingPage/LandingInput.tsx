// components/LandingPage/LandingInput.tsx
import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Send } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { LoginDialog } from '@/features/auth/components/LoginDialog'

export function LandingInput() {
  const [message, setMessage] = useState('')
  const [showLogin, setShowLogin] = useState(false)
  const navigate = useNavigate()

  const handleSend = useCallback(() => {
    const trimmedMessage = message.trim()
    if (!trimmedMessage) return
    setShowLogin(true)
  }, [message])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend]
  )

  return (
    <>
      <div className="relative">
        <input
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息开始对话..."
          autoFocus
          className="w-full rounded-2xl border-2 border-gray-200 bg-white px-6 py-4 pr-14 text-base transition-colors focus:border-blue-500 focus:outline-none dark:border-gray-800 dark:bg-gray-950"
        />
        <Button
          onClick={handleSend}
          disabled={!message.trim()}
          size="icon"
          className="absolute top-1/2 right-2 h-10 w-10 -translate-y-1/2 rounded-xl"
        >
          <Send className="h-5 w-5" />
        </Button>
      </div>

      <p className="mt-6 text-center text-sm text-gray-500 dark:text-gray-400">
        点击发送后，您需要登录或注册账号
      </p>

      <LoginDialog
        open={showLogin}
        onOpenChange={setShowLogin}
        onSuccess={() => {
          const encodedMsg = encodeURIComponent(message.trim())
          navigate(`/chat?msg=${encodedMsg}`)
        }}
      />
    </>
  )
}
