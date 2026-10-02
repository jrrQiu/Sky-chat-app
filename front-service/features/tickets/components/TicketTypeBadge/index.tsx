// features/tickets/components/TicketTypeBadge/index.tsx
import { ticketTypeLabel } from '@/features/tickets/types'

/** ITSM 惯例配色：事件红、服务请求蓝、问题琥珀、变更绿。 */
const TYPE_STYLES: Record<string, string> = {
  incident:
    'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-400 dark:border-red-900',
  request:
    'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-400 dark:border-blue-900',
  problem:
    'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-900',
  change:
    'bg-green-50 text-green-700 border-green-200 dark:bg-green-950/40 dark:text-green-400 dark:border-green-900',
}

const FALLBACK_STYLE =
  'bg-gray-50 text-gray-600 border-gray-200 dark:bg-gray-900 dark:text-gray-400 dark:border-gray-800'

export function TicketTypeBadge({
  type,
  className = '',
}: {
  type: string
  className?: string
}) {
  const style = TYPE_STYLES[type] ?? FALLBACK_STYLE

  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${style} ${className}`}
    >
      {ticketTypeLabel(type)}
    </span>
  )
}
