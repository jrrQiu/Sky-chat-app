// features/tickets/components/TicketList/index.tsx
import { Inbox, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { TicketListItem } from '@/features/tickets/components/TicketListItem'
import type { TicketSummary } from '@/features/tickets/types'

interface TicketListProps {
  items: TicketSummary[]
  loading: boolean
  error: string | null
  currentUserId?: string
  onOpen: (ticketId: string) => void
  emptyTitle?: string
  emptyHint?: string
  onRetry?: () => void
}

/** 列表本体：加载中 / 出错 / 空 / 有数据四种状态都在这里收口。 */
export function TicketList({
  items,
  loading,
  error,
  currentUserId,
  onOpen,
  emptyTitle = '暂无工单',
  emptyHint = '调整筛选条件，或新建一个工单',
  onRetry,
}: TicketListProps) {
  if (loading && items.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载工单列表…
      </div>
    )
  }

  if (error && !loading) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 dark:border-red-900 dark:bg-red-950/30">
        <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
        {onRetry && (
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            className="mt-2 border-red-200 text-red-700 hover:bg-red-100 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950/40"
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            重试
          </Button>
        )}
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <Inbox className="h-8 w-8 text-gray-300 dark:text-gray-600" />
        <p className="text-sm text-gray-500 dark:text-gray-400">{emptyTitle}</p>
        <p className="text-xs text-gray-400 dark:text-gray-500">{emptyHint}</p>
      </div>
    )
  }

  return (
    <div className="space-y-2.5">
      {items.map((ticket) => (
        <TicketListItem
          key={ticket.id}
          ticket={ticket}
          currentUserId={currentUserId}
          onOpen={onOpen}
        />
      ))}
      {loading && (
        <p className="py-2 text-center text-xs text-gray-400 dark:text-gray-500">
          正在刷新…
        </p>
      )}
    </div>
  )
}
