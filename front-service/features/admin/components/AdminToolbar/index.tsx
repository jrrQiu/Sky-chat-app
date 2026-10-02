// features/admin/components/AdminToolbar/index.tsx
import { useEffect, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  MailPlus,
  RefreshCw,
  Search,
  UserPlus,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  MEMBER_STATUS_LABELS,
  SEARCH_DEBOUNCE_MS,
  type MemberStatusFilter,
} from '@/features/admin/types'

interface AdminToolbarProps {
  query: string
  statusFilter: MemberStatusFilter
  /** 0 基页码，与后端一致。 */
  page: number
  size: number
  total: number
  loading: boolean
  onQueryChange: (query: string) => void
  onStatusFilterChange: (filter: MemberStatusFilter) => void
  onPageChange: (page: number) => void
  onRefresh: () => void
  onCreateMember: () => void
  onInviteMember: () => void
}

const STATUS_FILTERS: { value: MemberStatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: MEMBER_STATUS_LABELS.active },
  { value: 'disabled', label: MEMBER_STATUS_LABELS.disabled },
]

/**
 * 成员管理工具栏。
 *
 * 搜索框在本地维护草稿值并做防抖，避免每敲一个字就打一次接口；
 * 防抖结束后才把值交给 store，由 store 统一回到第一页并重新拉取。
 */
export function AdminToolbar({
  query,
  statusFilter,
  page,
  size,
  total,
  loading,
  onQueryChange,
  onStatusFilterChange,
  onPageChange,
  onRefresh,
  onCreateMember,
  onInviteMember,
}: AdminToolbarProps) {
  const [draft, setDraft] = useState(query)

  // 外部改动（刷新、切筛选）时同步草稿，保证输入框与服务端查询一致。
  useEffect(() => {
    setDraft(query)
  }, [query])

  useEffect(() => {
    if (draft === query) return
    const timer = window.setTimeout(() => onQueryChange(draft), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [draft, query, onQueryChange])

  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, size)))

  return (
    <div className="flex flex-col gap-3 border-b border-gray-200 pb-4 dark:border-gray-800">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-gray-400 dark:text-gray-500" />
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="搜索邮箱或姓名"
            className="pl-8"
            aria-label="搜索成员"
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

        <div className="inline-flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-900">
          {STATUS_FILTERS.map((item) => {
            const active = statusFilter === item.value
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => onStatusFilterChange(item.value)}
                className={`rounded-md px-2.5 py-1.5 text-xs transition-colors ${
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

        <Button variant="outline" size="sm" onClick={onInviteMember}>
          <MailPlus className="mr-1.5 h-3.5 w-3.5" />
          发送邀请
        </Button>

        <Button size="sm" onClick={onCreateMember}>
          <UserPlus className="mr-1.5 h-3.5 w-3.5" />
          新建成员
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
        <span>
          共 {total} 人
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
