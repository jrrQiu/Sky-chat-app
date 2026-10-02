import { ThemeProvider } from 'next-themes'
import { Toaster } from '@/components/ui/sonner'

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
    >
      {children}
      {/* 全局 toast 容器：成员管理/审批中心的操作反馈都依赖它 */}
      <Toaster position="top-center" />
    </ThemeProvider>
  )
}
