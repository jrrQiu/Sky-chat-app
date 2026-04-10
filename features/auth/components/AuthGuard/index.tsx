// features/auth/components/AuthGuard/index.tsx
//页面守卫组件，校验登录状态
'use client'

import { useAuth } from '@/features/auth/hooks/use-auth'
import { LoginDialog } from '../LoginDialog'
import { Loader2 } from 'lucide-react'

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isLoading, isAuthenticated, showLoginDialog } = useAuth()

  // 1. 正在检查 Cookie 里的 Token，显示全局 Loading
  if (isLoading) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  // 2. 如果确认没登录，显示半透明的聊天框背景，并在最上层强制弹出不可关闭的登录框
  if (!isAuthenticated) {
    return (
      <>
        {/* 在背后渲染一个模糊的空壳，假装里面有聊天，但不能点 */}
        <div className="pointer-events-none opacity-20 blur-sm h-screen w-full bg-white dark:bg-gray-900" />
        {/* 强制覆盖登录弹窗 */}
        <LoginDialog open={showLoginDialog} />
      </>
    )
  }

  // 3. 校验通过，正常渲染里面的内容！
  return <>{children}</>
}