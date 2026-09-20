// components/Sidebar/index.tsx
import { PanelLeftClose, PanelLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useUIStore } from '@/lib/stores/ui.store'
import { NewChatButton } from '@/features/conversation/components/NewChatButton'
import { ThemeToggle } from '@/components/ThemeToggle'
interface SidebarProps {
  children?: React.ReactNode // 里面会放历史会话列表
}

export function Sidebar({ children }: SidebarProps) {
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
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

      <div className="px-3 pb-3">
        <NewChatButton />
      </div>

      {!collapsed && (
        <ScrollArea className="flex-1 min-h-0 px-3">
          {children}
        </ScrollArea>
      )}
      <div className="mt-auto p-4 border-t border-gray-200 dark:border-gray-800">
        <div className={collapsed ? "flex justify-center" : "flex items-center px-2"}>
          <ThemeToggle />
          {!collapsed && <span className="ml-2 text-sm text-gray-600 dark:text-gray-400">切换主题</span>}
        </div>
      </div>
    </aside>
  )
}
