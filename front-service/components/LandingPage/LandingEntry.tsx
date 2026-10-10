// components/LandingPage/LandingEntry.tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Loader2, LogIn } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { LoginDialog } from '@/features/auth/components/LoginDialog'
import { useAuth } from '@/features/auth/hooks/use-auth'

/**
 * 落地页的入口：**一个按钮，不再是输入框**。
 *
 * 之前这里放的是聊天输入框，用户打完字点发送才弹登录框 —— 体感是"话说到一半被拦下来"，
 * 而且那条消息要跨越整个登录流程传递（先是 `?msg=` 挂到 `/chat`，再挂到 `/chat/{id}`），
 * 这既让"每次登录都新建会话"变得显眼，也是重复发送与消息丢失的来源。
 *
 * 现在入口只回答一个问题：进去，还是先登录。消息在对话页里输入，登录态在此之前就已经确定。
 */
export function LandingEntry() {
  const navigate = useNavigate()
  const [showLogin, setShowLogin] = useState(false)
  const { isAuthenticated, isLoading, user } = useAuth()

  const enterChat = () => navigate('/chat')

  const displayName = user?.name || user?.email || ''

  return (
    <div className="flex flex-col items-center gap-3">
      <Button
        size="lg"
        className="h-12 gap-2 rounded-xl px-8 text-base"
        disabled={isLoading}
        onClick={isAuthenticated ? enterChat : () => setShowLogin(true)}
      >
        {isLoading ? (
          <Loader2 className="h-5 w-5 animate-spin" />
        ) : isAuthenticated ? (
          <ArrowRight className="h-5 w-5" />
        ) : (
          <LogIn className="h-5 w-5" />
        )}
        {isLoading ? '正在检查登录状态…' : isAuthenticated ? '进入对话' : '登录 / 注册'}
      </Button>

      <p className="text-sm text-gray-500 dark:text-gray-400">
        {isAuthenticated
          ? `已登录${displayName ? `为 ${displayName}` : ''} · 将打开最近一次会话`
          : '登录后进入你的对话，默认打开最近一次会话'}
      </p>

      <LoginDialog
        open={showLogin}
        onOpenChange={setShowLogin}
        // 登录成功后进入 /chat，由它负责把用户带到最近的会话；这里不新建、也不带消息。
        onSuccess={enterChat}
      />
    </div>
  )
}
