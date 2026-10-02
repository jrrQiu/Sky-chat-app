// features/approvals/components/ApprovalToolbar/index.tsx
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type {
  ApprovalScope,
  ApprovalStatusFilter,
} from '@/features/approvals/types'

interface ApprovalToolbarProps {
  scope: ApprovalScope
  statusFilter: ApprovalStatusFilter
  counts: Record<ApprovalStatusFilter, number>
  pendingForMe: number
  onScopeChange: (scope: ApprovalScope) => void
  onStatusFilterChange: (filter: ApprovalStatusFilter) => void
  onRefresh: () => void
  loading: boolean
}

const SCOPES: { value: ApprovalScope; label: string }[] = [
  { value: 'all', label: '与我相关' },
  { value: 'to_approve', label: '待我审批' },
  { value: 'mine', label: '我发起的' },
]

const STATUSES: { value: ApprovalStatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已驳回' },
]

export function ApprovalToolbar({
  scope,
  statusFilter,
  counts,
  pendingForMe,
  onScopeChange,
  onStatusFilterChange,
  onRefresh,
  loading,
}: ApprovalToolbarProps) {
  return (
    <div className="flex flex-col gap-3 border-b border-gray-200 pb-4 dark:border-gray-800">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-900">
          {SCOPES.map((item) => {
            const active = scope === item.value
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => onScopeChange(item.value)}
                className={`relative rounded-md px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-gray-100'
                    : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                }`}
              >
                {item.label}
                {item.value === 'to_approve' && pendingForMe > 0 && (
                  <span className="ml-1.5 rounded-full bg-red-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                    {pendingForMe}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        <Button
          variant="ghost"
          size="sm"
          onClick={onRefresh}
          disabled={loading}
          className="text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
        >
          <RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {STATUSES.map((item) => {
          const active = statusFilter === item.value
          const count = counts[item.value] ?? 0
          return (
            <button
              key={item.value}
              type="button"
              onClick={() => onStatusFilterChange(item.value)}
              className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                active
                  ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-400'
                  : 'border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:border-gray-800 dark:text-gray-400 dark:hover:text-gray-200'
              }`}
            >
              {item.label}
              <span className="ml-1 text-[10px] opacity-70">{count}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
