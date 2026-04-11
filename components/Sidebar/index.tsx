// components/Sidebar/index.tsx
'use client'

import { PanelLeftClose, PanelLeft, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useUIStore } from '@/lib/stores/ui.store'
import { useRouter } from 'next/navigation'
import { NewChatButton } from '@/features/conversation/components/NewChatButton'

interface SidebarProps {
  children?: React.ReactNode // 里面会放历史会话列表
}

export function Sidebar({ children }: SidebarProps) {
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const router = useRouter()
  
  return (
    <aside
      className={`flex h-screen flex-col bg-gray-50 dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 transition-all duration-300 relative overflow-hidden ${
        collapsed ? 'w-16' : 'w-64'
      }`}
    >
      {/* 顶部：Logo 和 折叠按钮 */}
      <div className={`p-3 ${collapsed ? "flex flex-col items-center gap-2" : "flex items-center justify-between px-4"}`}>
        {!collapsed && (
          <span className="font-semibold text-lg whitespace-nowrap bg-gradient-to-r from-blue-400 to-blue-600 bg-clip-text text-transparent">
            Sky Chat
          </span>
        )}
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          className="h-8 w-8 hover:bg-gray-200 dark:hover:bg-gray-800 shrink-0"
        >
          {collapsed ? <PanelLeft className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </Button>
      </div>

      {/* 新建对话按钮 */}
      <div className="px-3 pb-3">
        <NewChatButton /> {/* <--- 替换掉原来写死的 <Button> */}
      </div>

      {/* 中间：历史会话列表区域 */}
      {!collapsed && (
        <ScrollArea className="flex-1 min-h-0 px-3">
          {children}
        </ScrollArea>
      )}
      
      {/* 底部预留：可以放用户头像、设置等 */}
    </aside>
  )
}