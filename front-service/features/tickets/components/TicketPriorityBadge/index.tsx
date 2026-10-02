// features/tickets/components/TicketPriorityBadge/index.tsx
import { ticketPriorityLabel } from '@/features/tickets/types'

/** 与审批中心的风险等级同一套配色：严重=红，高=琥珀，中=蓝，低=灰。 */
const PRIORITY_STYLES: Record<string, string> = {
  critical:
    'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-400 dark:border-red-900',
  high: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-900',
  medium:
    'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-400 dark:border-blue-900',
  low: 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-gray-900 dark:text-gray-400 dark:border-gray-800',
}

const FALLBACK_STYLE = PRIORITY_STYLES.low

export function TicketPriorityBadge({
  priority,
  className = '',
}: {
  priority: string
  className?: string
}) {
  const style = PRIORITY_STYLES[priority] ?? FALLBACK_STYLE

  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold ${style} ${className}`}
      title={`优先级：${ticketPriorityLabel(priority)}`}
    >
      {ticketPriorityLabel(priority)}
    </span>
  )
}
