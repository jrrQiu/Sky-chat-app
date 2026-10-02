// src/pages/TicketDetailPage.tsx
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { Ticket } from 'lucide-react'
import { MainLayout } from '@/components/MainLayout'
import { ScrollArea } from '@/components/ui/scroll-area'
import { TicketDetailPanel } from '@/features/tickets/components/TicketDetailPanel'

/**
 * 单个工单的路由页。
 *
 * 路由化让工单链接可分享；工单不可见时服务端返回 404，由 TicketDetailPanel
 * 渲染成"工单不存在"，不泄露该工单是否存在。
 */
export function TicketDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  if (!id) {
    return <Navigate to="/tickets" replace />
  }

  return (
    <MainLayout>
      <div className="flex h-full flex-col">
        <header className="border-b border-gray-200 px-6 py-4 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950/40 dark:text-blue-400">
              <Ticket className="h-4.5 w-4.5" />
            </div>
            <div>
              <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                工单详情
              </h1>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                状态流转、分派、关联与回复都会写入工单时间线
              </p>
            </div>
          </div>
        </header>

        <ScrollArea className="flex-1 min-h-0">
          <TicketDetailPanel
            ticketId={id}
            onBack={() => navigate('/tickets')}
            onOpenTicket={(ticketId) => navigate(`/tickets/${ticketId}`)}
          />
        </ScrollArea>
      </div>
    </MainLayout>
  )
}
