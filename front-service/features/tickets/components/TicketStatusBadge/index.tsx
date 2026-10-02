// features/tickets/components/TicketStatusBadge/index.tsx
import { ticketStatusLabel } from '@/features/tickets/types'

/**
 * 状态配色：新建/已受理走蓝色系，处理中走琥珀，等待中走深灰，已解决走绿色，已关闭走浅灰。
 * 终态刻意用更淡的灰，让"还需要盯"的工单在列表里跳出来。
 */
const STATUS_STYLES: Record<string, string> = {
  new: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-400 dark:border-blue-900',
  open: 'bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-800',
  in_progress:
    'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-900',
  pending:
    'bg-gray-100 text-gray-700 border-gray-300 dark:bg-gray-800 dark:text-gray-300 dark:border-gray-700',
  resolved:
    'bg-green-50 text-green-700 border-green-200 dark:bg-green-950/40 dark:text-green-400 dark:border-green-900',
  closed:
    'bg-gray-50 text-gray-500 border-gray-200 dark:bg-gray-900 dark:text-gray-500 dark:border-gray-800',
}

const FALLBACK_STYLE = STATUS_STYLES.closed

export function TicketStatusBadge({
  status,
  className = '',
}: {
  status: string
  className?: string
}) {
  const style = STATUS_STYLES[status] ?? FALLBACK_STYLE

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${style} ${className}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {ticketStatusLabel(status)}
    </span>
  )
}
