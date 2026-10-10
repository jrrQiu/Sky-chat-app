// features/auth/components/AuthGuard/index.tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/features/auth/hooks/use-auth'
import { LoginDialog } from '../LoginDialog'

/**
 * 受保护路由的守卫。
 *
 * 这里原先有两处体验缺陷，都是这次修掉的：
 *
 * 1. **登录成功后整页刷新**（`onSuccess={() => window.location.reload()}`）。登录只是把
 *    会话写进 zustand store，而 store 一更新这个守卫就会重新渲染并放行 —— 刷新是多余的，
 *    代价是丢掉当前页其余状态、并把所有资源重新拉一遍。
 * 2. **登录框关不掉**（`onOpenChange={() => {}}`）。用户在受保护页面上按 Esc / 点遮罩都
 *    没反应；即便关掉了，背后也只是一个模糊的空屏，等于死胡同。现在关掉会落到一个明确的
 *    「请先登录」面板：可以主动再打开登录框，也可以返回首页 —— 和落地页「按钮 + 弹框」
 *    的形态保持一致。
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isLoading, isAuthenticated } = useAuth()
  const navigate = useNavigate()
  const [showLogin, setShowLogin] = useState(false)

  // 进入受保护路由先弹一次登录框：用户点进来就是想用这个页面。
  // 关掉之后不再自动弹回（依赖没变），面板里有按钮可以主动打开。
  useEffect(() => {
    if (!isLoading && !isAuthenticated) setShowLogin(true)
  }, [isLoading, isAuthenticated])

  if (isLoading) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-gray-50 px-6 dark:bg-gray-900">
        <div className="w-full max-w-sm space-y-4 rounded-2xl border border-gray-200 bg-white p-8 text-center dark:border-gray-800 dark:bg-gray-950">
          <Lock className="mx-auto h-8 w-8 text-gray-400" />
          <div className="space-y-1">
            <p className="font-medium text-gray-900 dark:text-gray-100">请先登录后继续</p>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              登录后即可查看会话、审批与工单。
            </p>
          </div>
          <div className="flex justify-center gap-2">
            <Button onClick={() => setShowLogin(true)}>登录 / 注册</Button>
            <Button variant="outline" onClick={() => navigate('/')}>
              返回首页
            </Button>
          </div>
        </div>

        <LoginDialog
          open={showLogin}
          onOpenChange={setShowLogin}
          // 登录成功后不需要刷新：store 更新会触发这个守卫重渲染，并把 children 渲染出来。
          onSuccess={() => setShowLogin(false)}
        />
      </div>
    )
  }

  return <>{children}</>
}
