// components/MainLayout/index.tsx
import { ReactNode } from 'react'
import { Sidebar } from '../Sidebar'

interface MainLayoutProps {
  sidebarChildren?: ReactNode // 传给侧边栏的内容（比如历史列表）
  children: ReactNode         // 右侧主聊天区
}

export function MainLayout({ sidebarChildren, children }: MainLayoutProps) {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-white dark:bg-gray-950">
      {/* 左侧：侧边栏 */}
      <Sidebar>
        {sidebarChildren}
      </Sidebar>

      {/* 右侧：主内容区 */}
      <div className="flex flex-1 flex-col overflow-hidden relative">
        {children}
      </div>
    </div>
  )
}