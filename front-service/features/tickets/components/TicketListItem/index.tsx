// features/tickets/components/TicketListItem/index.tsx
import { ChevronRight, Ticket, UserRound } from 'lucide-react'
import { TicketPriorityBadge } from '@/features/tickets/components/TicketPriorityBadge'
import { TicketStatusBadge } from '@/features/tickets/components/TicketStatusBadge'
import { TicketTypeBadge } from '@/features/tickets/components/TicketTypeBadge'
import {
  formatTicketNumber,
  formatTicketRelativeTime,
  type TicketSummary,
} from '@/features/tickets/types'

interface TicketListItemProps {
  ticket: TicketSummary
  /** 当前登录用户 id，用来标出"我发起的 / 指派给我"。 */
  currentUserId?: string
  onOpen: (ticketId: string) => void
}

export function TicketListItem({ ticket, currentUserId, onOpen }: TicketListItemProps) {
  const isRequester = Boolean(currentUserId) && ticket.requesterId === currentUserId
  const isAssignee = Boolean(currentUserId) && ticket.assigneeId === currentUserId
  const isMerged = Boolean(ticket.mergedIntoId)

  return (
    <button
      type="button"
      onClick={() => onOpen(ticket.id)}
      className="group flex w-full items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/40 focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:outline-none dark:border-gray-800 dark:bg-gray-900 dark:hover:border-blue-800 dark:hover:bg-blue-950/20"
    >
      <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
        <Ticket className="h-4 w-4" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="shrink-0 font-mono text-xs text-gray-400 dark:text-gray-500">
            {formatTicketNumber(ticket.number)}
          </span>
          <span
            className={`truncate text-sm font-medium ${
              isMerged
                ? 'text-gray-400 line-through dark:text-gray-500'
                : 'text-gray-900 dark:text-gray-100'
            }`}
          >
            {ticket.title}
          </span>
          <TicketTypeBadge type={ticket.type} />
          <TicketStatusBadge status={ticket.status} />
          <TicketPriorityBadge priority={ticket.priority} />
          {isMerged && (
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
              已合并
            </span>
          )}
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
          <span className="inline-flex items-center gap-1">
            <UserRound className="h-3 w-3" />
            {ticket.assigneeId ? `处理人 ${ticket.assigneeId}` : '未分派'}
          </span>
          <span>请求人 {ticket.requesterId}</span>
          {isAssignee && (
            <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] text-blue-600 dark:bg-blue-950/40 dark:text-blue-400">
              指派给我
            </span>
          )}
          {isRequester && (
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
              我发起的
            </span>
          )}
          {ticket.firstResponseAt ? (
            <span className="text-green-600 dark:text-green-400">已首次响应</span>
          ) : (
            <span className="text-amber-600 dark:text-amber-400">待首次响应</span>
          )}
          <span>{formatTicketRelativeTime(ticket.updatedAt ?? ticket.createdAt)}</span>
        </div>
      </div>

      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-gray-300 transition-transform group-hover:translate-x-0.5 group-hover:text-blue-500 dark:text-gray-600" />
    </button>
  )
}
