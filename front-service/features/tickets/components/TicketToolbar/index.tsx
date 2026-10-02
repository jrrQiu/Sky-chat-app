// features/tickets/components/TicketToolbar/index.tsx
import { useEffect, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  SEARCH_DEBOUNCE_MS,
  TICKET_PRIORITIES,
  TICKET_STATUSES,
  TICKET_TYPES,
  ticketPriorityLabel,
  ticketStatusLabel,
  ticketTypeLabel,
  type TicketCounts,
  type TicketMeta,
  type TicketPriorityFilter,
  type TicketScope,
  type TicketStatusFilter,
  type TicketTypeFilter,
} from '@/features/tickets/types'

interface TicketToolbarProps {
  scope: TicketScope
  statusFilter: TicketStatusFilter
  typeFilter: TicketTypeFilter
  priorityFilter: TicketPriorityFilter
  query: string
  counts: TicketCounts
  total: number
  /** 0 基页码，与后端一致。 */
  page: number
  size: number
  loading: boolean
  meta: TicketMeta | null
  metaError?: string | null
  /** 当前用户是否是工单处理人，决定"我的工单"的语义提示。 */
  isAgent: boolean
  onScopeChange: (scope: TicketScope) => void
  onStatusFilterChange: (filter: TicketStatusFilter) => void
  onTypeFilterChange: (filter: TicketTypeFilter) => void
  onPriorityFilterChange: (filter: TicketPriorityFilter) => void
  onQueryChange: (query: string) => void
  onPageChange: (page: number) => void
  onRefresh: () => void
  onCreate: () => void
}

interface ChipOption {
  value: string
  label: string
  count?: number
}

const SCOPES: { value: TicketScope; label: string }[] = [
  { value: 'mine', label: '我的工单' },
  { value: 'all', label: '全部' },
]

function FilterChips({
  label,
  hint,
  options,
  value,
  disabled,
  onChange,
}: {
  label: string
  hint?: string
  options: ChipOption[]
  value: string
  disabled?: boolean
  onChange: (value: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="shrink-0 text-xs text-gray-400 dark:text-gray-500">{label}</span>
      {options.map((option) => {
        const active = value === option.value
        return (
          <button
            key={option.value}
            type="button"
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors disabled:opacity-50 ${
              active
                ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-400'
                : 'border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:border-gray-800 dark:text-gray-400 dark:hover:text-gray-200'
            }`}
          >
            {option.label}
            {option.count !== undefined && (
              <span className="ml-1 text-[10px] opacity-70">{option.count}</span>
            )}
          </button>
        )
      })}
      {hint && (
        <span className="text-[11px] text-gray-400 dark:text-gray-500">{hint}</span>
      )}
    </div>
  )
}

/**
 * 工单工具栏。
 *
 * 枚举一律优先用 meta，meta 拿不到时才回退到内置列表；
 * 搜索框本地维护草稿并防抖 350ms，避免每敲一个字就打一次接口。
 */
export function TicketToolbar({
  scope,
  statusFilter,
  typeFilter,
  priorityFilter,
  query,
  counts,
  total,
  page,
  size,
  loading,
  meta,
  metaError,
  isAgent,
  onScopeChange,
  onStatusFilterChange,
  onTypeFilterChange,
  onPriorityFilterChange,
  onQueryChange,
  onPageChange,
  onRefresh,
  onCreate,
}: TicketToolbarProps) {
  const [draft, setDraft] = useState(query)

  // 外部改动（刷新、清空筛选）时同步草稿，保证输入框与服务端查询一致。
  useEffect(() => {
    setDraft(query)
  }, [query])

  useEffect(() => {
    if (draft === query) return
    const timer = window.setTimeout(() => onQueryChange(draft), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [draft, query, onQueryChange])

  // meta 是权威来源，拿不到时才回退到内置枚举。
  const statuses: string[] = meta?.statuses?.length ? meta.statuses : [...TICKET_STATUSES]
  const types: string[] = meta?.types?.length ? meta.types : [...TICKET_TYPES]
  const priorities: string[] = meta?.priorities?.length
    ? meta.priorities
    : [...TICKET_PRIORITIES]

  // 各状态计数来自服务端；"全部"用它们的和，避免在筛选状态下把当前页 total 当成总数。
  const statusTotal = Object.values(counts.byStatus ?? {}).reduce(
    (sum, value) => sum + value,
    0
  )

  const statusOptions: ChipOption[] = [
    { value: 'all', label: '全部', count: statusTotal > 0 ? statusTotal : total },
    ...statuses.map((status) => ({
      value: status,
      label: ticketStatusLabel(status),
      count: counts.byStatus?.[status] ?? 0,
    })),
  ]

  const typeOptions: ChipOption[] = [
    { value: 'all', label: '全部' },
    ...types.map((type) => ({ value: type, label: ticketTypeLabel(type) })),
  ]

  const priorityOptions: ChipOption[] = [
    { value: 'all', label: '全部' },
    ...priorities.map((priority) => ({
      value: priority,
      label: ticketPriorityLabel(priority),
      count: counts.byPriority?.[priority] ?? 0,
    })),
  ]

  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, size)))

  return (
    <div className="flex flex-col gap-3 border-b border-gray-200 pb-4 dark:border-gray-800">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-gray-400 dark:text-gray-500" />
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="搜索标题、编号或描述"
            className="pl-8"
            aria-label="搜索工单"
          />
          {draft.length > 0 && (
            <button
              type="button"
              onClick={() => setDraft('')}
              aria-label="清空搜索"
              className="absolute top-1/2 right-2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <div
          className="inline-flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-900"
          title={
            isAgent
              ? '我的工单 = 指派给我；全部 = 服务端返回的全部可见工单'
              : '我的工单 = 我提交的；非处理人只能看到自己的工单'
          }
        >
          {SCOPES.map((item) => {
            const active = scope === item.value
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => onScopeChange(item.value)}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-gray-100'
                    : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                }`}
              >
                {item.label}
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
          {loading ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          刷新
        </Button>

        <Button size="sm" onClick={onCreate}>
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          新建工单
        </Button>
      </div>

      <FilterChips
        label="状态"
        options={statusOptions}
        value={statusFilter}
        disabled={loading}
        onChange={(value) => onStatusFilterChange(value as TicketStatusFilter)}
      />

      <FilterChips
        label="类型"
        options={typeOptions}
        value={typeFilter}
        disabled={loading}
        onChange={(value) => onTypeFilterChange(value as TicketTypeFilter)}
      />

      <FilterChips
        label="优先级"
        hint={
          priorityFilter === 'all'
            ? '优先级按当前页过滤'
            : '仅过滤当前页已加载的数据'
        }
        options={priorityOptions}
        value={priorityFilter}
        onChange={(value) => onPriorityFilterChange(value as TicketPriorityFilter)}
      />

      {metaError && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-400">
          {metaError}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
        <span>
          共 {total} 条
          {loading && <span className="ml-1.5 opacity-70">正在加载…</span>}
        </span>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => onPageChange(page - 1)}
            disabled={loading || page <= 0}
            aria-label="上一页"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <span className="tabular-nums">
            第 {Math.min(page + 1, totalPages)} / {totalPages} 页
          </span>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => onPageChange(page + 1)}
            disabled={loading || page >= totalPages - 1}
            aria-label="下一页"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  )
}
