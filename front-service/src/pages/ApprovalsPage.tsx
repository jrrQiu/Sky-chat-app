// src/pages/ApprovalsPage.tsx
import { useEffect, useMemo } from 'react'
import { Inbox, Loader2, ShieldAlert } from 'lucide-react'
import { toast } from 'sonner'
import { MainLayout } from '@/components/MainLayout'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ApprovalDetailDialog } from '@/features/approvals/components/ApprovalDetailDialog'
import { ApprovalListItem } from '@/features/approvals/components/ApprovalListItem'
import { ApprovalToolbar } from '@/features/approvals/components/ApprovalToolbar'
import { useApprovalStore } from '@/features/approvals/store/approval.store'
import type { ApprovalStatusFilter } from '@/features/approvals/types'
import { useAuthStore } from '@/features/auth/store/auth.store'

export function ApprovalsPage() {
  const user = useAuthStore((state) => state.user)

  const items = useApprovalStore((state) => state.items)
  const scope = useApprovalStore((state) => state.scope)
  const statusFilter = useApprovalStore((state) => state.statusFilter)
  const loading = useApprovalStore((state) => state.loading)
  const error = useApprovalStore((state) => state.error)
  const activeId = useApprovalStore((state) => state.activeId)
  const pendingForMe = useApprovalStore((state) => state.pendingForMe)
  const load = useApprovalStore((state) => state.load)
  const setScope = useApprovalStore((state) => state.setScope)
  const setStatusFilter = useApprovalStore((state) => state.setStatusFilter)
  const openDetail = useApprovalStore((state) => state.openDetail)
  const closeDetail = useApprovalStore((state) => state.closeDetail)
  const decide = useApprovalStore((state) => state.decide)

  useEffect(() => {
    void load()
  }, [load])

  const counts = useMemo(() => {
    const base: Record<ApprovalStatusFilter, number> = {
      all: items.length,
      pending: 0,
      approved: 0,
      rejected: 0,
    }
    for (const item of items) {
      if (item.status === 'pending') base.pending += 1
      else if (item.status === 'approved') base.approved += 1
      else if (item.status === 'rejected') base.rejected += 1
    }
    return base
  }, [items])

  const visible = useMemo(
    () =>
      statusFilter === 'all'
        ? items
        : items.filter((item) => item.status === statusFilter),
    [items, statusFilter]
  )

  const activeApproval = useMemo(
    () => items.find((item) => item.id === activeId) ?? null,
    [items, activeId]
  )

  async function handleDecide(
    approvalId: string,
    action: 'approve' | 'reject',
    comment?: string
  ) {
    const result = await decide(approvalId, action, comment)
    if (result.ok) {
      toast.success(result.message)
    } else {
      toast.error(result.message)
    }
    return result
  }

  return (
    <MainLayout>
      <div className="flex h-full flex-col">
        <header className="border-b border-gray-200 px-6 py-4 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950/40 dark:text-blue-400">
              <ShieldAlert className="h-4.5 w-4.5" />
            </div>
            <div>
              <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                审批中心
              </h1>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                高风险操作需要人工确认，审批记录会写入审计日志
              </p>
            </div>
          </div>
        </header>

        <div className="px-6 pt-4">
          <ApprovalToolbar
            scope={scope}
            statusFilter={statusFilter}
            counts={counts}
            pendingForMe={pendingForMe}
            onScopeChange={setScope}
            onStatusFilterChange={setStatusFilter}
            onRefresh={() => void load()}
            loading={loading}
          />
        </div>

        <ScrollArea className="flex-1 min-h-0">
          <div className="space-y-2.5 px-6 py-4">
            {loading && visible.length === 0 && (
              <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-400">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在加载审批列表…
              </div>
            )}

            {error && !loading && (
              <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-400">
                {error}
              </div>
            )}

            {!loading && !error && visible.length === 0 && (
              <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
                <Inbox className="h-8 w-8 text-gray-300 dark:text-gray-600" />
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {scope === 'to_approve'
                    ? '当前没有需要你处理的审批'
                    : '暂无审批记录'}
                </p>
                <p className="text-xs text-gray-400 dark:text-gray-500">
                  当高风险操作触发审批规则时，这里会出现待办
                </p>
              </div>
            )}

            {visible.map((approval) => (
              <ApprovalListItem
                key={approval.id}
                approval={approval}
                currentUserId={user?.id}
                onOpen={openDetail}
              />
            ))}
          </div>
        </ScrollArea>
      </div>

      <ApprovalDetailDialog
        approval={activeApproval}
        open={activeApproval !== null}
        onOpenChange={(open) => {
          if (!open) closeDetail()
        }}
        onDecide={handleDecide}
      />
    </MainLayout>
  )
}
