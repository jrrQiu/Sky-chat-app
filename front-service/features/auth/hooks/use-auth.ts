import { useEffect } from 'react'
import { useAuthStore } from '@/features/auth/store/auth.store'

export function useAuth() {
  const { status, user, logout, loadFromStorage } = useAuthStore()

  useEffect(() => {
    loadFromStorage()
  }, [loadFromStorage])

  const isLoading = status === 'loading'
  const isAuthenticated = status === 'authenticated'

  // 说明：这里曾经导出 `showLoginDialog`（`status === 'unauthenticated'`），但唯一的调用方
  // AuthGuard 已经改成自己管理弹框开关 —— 守卫关掉弹框后要留在"请先登录"面板上，而不是被
  // 这个派生值立刻弹回来。留着它会诱使下一个调用方写出同样的死循环式交互，所以移除。
  return {
    isAuthenticated,
    user,
    isLoading,
    logout,
  }
}
