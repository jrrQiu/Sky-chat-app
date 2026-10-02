// src/pages/TicketsPage.tsx
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Ticket } from 'lucide-react'
import { toast } from 'sonner'
import { MainLayout } from '@/components/MainLayout'
import { ScrollArea } from '@/components/ui/scroll-area'
import { CreateTicketDialog } from '@/features/tickets/components/CreateTicketDialog'
import { TicketList } from '@/features/tickets/components/TicketList'
import { TicketToolbar } from '@/features/tickets/components/TicketToolbar'
import { useTicketStore } from '@/features/tickets/store/ticket.store'
import {
  canActAsAgent,
  matchesPriorityFilter,
  type CreateTicketRequest,
} from '@/features/tickets/types'
import { useAuthStore } from '@/features/auth/store/auth.store'

/**
 * 工单列表页。
 *
 * 交互模型固定为"列表 + 每个工单一个路由"：点列表进入 /tickets/:id，
 * 这样工单链接可以直接分享给别人，也不用维护弹窗和路由两套详情渲染。
 */
export function TicketsPage() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)

  const items = useTicketStore((state) => state.items)
  const total = useTicketStore((state) => state.total)
  const page = useTicketStore((state) => state.page)
  const size = useTicketStore((state) => state.size)
  const counts = useTicketStore((state) => state.counts)
  const scope = useTicketStore((state) => state.scope)
  const statusFilter = useTicketStore((state) => state.statusFilter)
  const typeFilter = useTicketStore((state) => state.typeFilter)
  const priorityFilter = useTicketStore((state) => state.priorityFilter)
  const query = useTicketStore((state) => state.query)
  const loading = useTicketStore((state) => state.loading)
  const error = useTicketStore((state) => state.error)
  const meta = useTicketStore((state) => state.meta)
  const metaError = useTicketStore((state) => state.metaError)
  const creating = useTicketStore((state) => state.creating)

  const load = useTicketStore((state) => state.load)
  const loadMeta = useTicketStore((state) => state.loadMeta)
  const setScope = useTicketStore((state) => state.setScope)
  const setStatusFilter = useTicketStore((state) => state.setStatusFilter)
  const setTypeFilter = useTicketStore((state) => state.setTypeFilter)
  const setPriorityFilter = useTicketStore((state) => state.setPriorityFilter)
  const setQuery = useTicketStore((state) => state.setQuery)
  const setPage = useTicketStore((state) => state.setPage)
  const createTicket = useTicketStore((state) => state.createTicket)

  const [createOpen, setCreateOpen] = useState(false)

  useEffect(() => {
    void loadMeta()
    void load()
  }, [load, loadMeta])

  const isAgent = canActAsAgent(user?.roles, meta?.agentRoles)

  // 优先级只过滤当前页已加载的数据：列表接口没有 priority 参数。
  const visible = useMemo(
    () => items.filter((ticket) => matchesPriorityFilter(ticket, priorityFilter)),
    [items, priorityFilter]
  )

  const hasFilter =
    statusFilter !== 'all' ||
    typeFilter !== 'all' ||
    priorityFilter !== 'all' ||
    query.trim().length > 0

  const emptyTitle = hasFilter
    ? '没有符合筛选条件的工单'
    : scope === 'mine'
      ? isAgent
        ? '当前没有指派给你的工单'
        : '你还没有提交过工单'
      : '暂无工单'

  const emptyHint = hasFilter
    ? '试着放宽状态、类型或优先级条件'
    : '新建工单后，处理人会在工单队列里看到它'

  async function handleCreate(payload: CreateTicketRequest) {
    const result = await createTicket(payload)
    if (!result.ok) {
      toast.error(result.message)
      return result
    }

    toast.success(result.message)
    if (result.ticket) {
      navigate(`/tickets/${result.ticket.id}`)
    }
    return result
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
                工单
              </h1>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                事件 / 服务请求 / 问题 / 变更统一在这里流转，状态由服务端状态机校验
              </p>
            </div>
          </div>
        </header>

        <div className="px-6 pt-4">
          <TicketToolbar
            scope={scope}
            statusFilter={statusFilter}
            typeFilter={typeFilter}
            priorityFilter={priorityFilter}
            query={query}
            counts={counts}
            total={total}
            page={page}
            size={size}
            loading={loading}
            meta={meta}
            metaError={metaError}
            isAgent={isAgent}
            onScopeChange={setScope}
            onStatusFilterChange={setStatusFilter}
            onTypeFilterChange={setTypeFilter}
            onPriorityFilterChange={setPriorityFilter}
            onQueryChange={setQuery}
            onPageChange={setPage}
            onRefresh={() => void load()}
            onCreate={() => setCreateOpen(true)}
          />
        </div>

        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4">
            <TicketList
              items={visible}
              loading={loading}
              error={error}
              currentUserId={user?.id}
              onOpen={(ticketId) => navigate(`/tickets/${ticketId}`)}
              emptyTitle={emptyTitle}
              emptyHint={emptyHint}
              onRetry={() => void load()}
            />

            {priorityFilter !== 'all' && visible.length > 0 && (
              <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
                优先级筛选只作用在当前页；共 {total} 条工单，本页显示 {visible.length} 条。
              </p>
            )}
          </div>
        </ScrollArea>
      </div>

      <CreateTicketDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        meta={meta}
        submitting={creating}
        onSubmit={handleCreate}
      />
    </MainLayout>
  )
}
