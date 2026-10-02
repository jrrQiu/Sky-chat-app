// components/Sidebar/SidebarNav.tsx
import { useEffect } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { MessagesSquare, ShieldCheck, UsersRound } from 'lucide-react'
import { useApprovalStore } from '@/features/approvals/store/approval.store'
import { hasAdminRole } from '@/features/admin/types'
import { useAuthStore } from '@/features/auth/store/auth.store'

interface SidebarNavProps {
  collapsed: boolean
}

const BADGE_POLL_MS = 60_000

/**
 * 侧边栏主导航。
 *
 * 待办角标按需拉取而不是靠常驻轮询：进入页面时取一次，之后每 60 秒刷新一次。
 * 审批是低频操作，这个频率既能让角标准确，又不会给后端增加无谓压力。
 */
export function SidebarNav({ collapsed }: SidebarNavProps) {
  const pendingForMe = useApprovalStore((state) => state.pendingForMe)
  const refreshBadge = useApprovalStore((state) => state.refreshBadge)
  const user = useAuthStore((state) => state.user)
  const location = useLocation()

  useEffect(() => {
    void refreshBadge()
    const timer = window.setInterval(() => {
      void refreshBadge()
    }, BADGE_POLL_MS)
    return () => window.clearInterval(timer)
    // 切换路由时立刻校准一次角标。
  }, [refreshBadge, location.pathname])

  // 成员管理只对管理员可见；服务端会对每个接口再校验一次角色。
  const items = [
    { to: '/chat', label: '对话', icon: MessagesSquare },
    { to: '/approvals', label: '审批中心', icon: ShieldCheck },
    ...(hasAdminRole(user?.roles)
      ? [{ to: '/admin/users', label: '成员管理', icon: UsersRound }]
      : []),
  ]

  return (
    <nav className="flex flex-col gap-0.5 px-3 pb-2">
      {items.map((item) => {
        const Icon = item.icon
        const showBadge = item.to === '/approvals' && pendingForMe > 0
        return (
          <NavLink
            key={item.to}
            to={item.to}
            title={collapsed ? item.label : undefined}
            className={({ isActive }) =>
              `group relative flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors ${
                collapsed ? 'justify-center px-0' : ''
              } ${
                isActive
                  ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                  : 'text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
              }`
            }
          >
            <Icon className="h-4 w-4 shrink-0 opacity-80" />
            {!collapsed && <span className="truncate">{item.label}</span>}
            {showBadge &&
              (collapsed ? (
                <span className="absolute top-1.5 right-2 h-2 w-2 rounded-full bg-red-500" />
              ) : (
                <span className="ml-auto rounded-full bg-red-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                  {pendingForMe > 99 ? '99+' : pendingForMe}
                </span>
              ))}
          </NavLink>
        )
      })}
    </nav>
  )
}
