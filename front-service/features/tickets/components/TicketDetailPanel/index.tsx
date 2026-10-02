// features/tickets/components/TicketDetailPanel/index.tsx
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  Loader2,
  MessagesSquare,
  Pencil,
  RefreshCw,
  UserPlus,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { TicketCommentComposer } from '@/features/tickets/components/TicketCommentComposer'
import { TicketLinksPanel } from '@/features/tickets/components/TicketLinksPanel'
import { TicketPriorityBadge } from '@/features/tickets/components/TicketPriorityBadge'
import { TicketStatusBadge } from '@/features/tickets/components/TicketStatusBadge'
import { TicketTimeline, TicketTimelineHeader } from '@/features/tickets/components/TicketTimeline'
import { TicketTypeBadge } from '@/features/tickets/components/TicketTypeBadge'
import { useTicketStore } from '@/features/tickets/store/ticket.store'
import {
  allowedTransitions,
  canActAsAgent,
  formatTicketDateTime,
  formatTicketNumber,
  formatTicketRelativeTime,
  isInternalComment,
  TICKET_IMPACT_LABELS,
  TICKET_IMPACT_LEVELS,
  TICKET_NOT_FOUND_MESSAGE,
  TICKET_TYPES,
  TICKET_URGENCY_LABELS,
  TICKET_URGENCY_LEVELS,
  ticketSourceLabel,
  ticketStatusLabel,
  ticketTypeLabel,
  type AddTicketCommentRequest,
  type TicketDetail,
  type TicketImpact,
  type TicketMeta,
  type TicketType,
  type TicketUrgency,
  type UpdateTicketRequest,
} from '@/features/tickets/types'
import { useAuthStore } from '@/features/auth/store/auth.store'

interface TicketDetailPanelProps {
  ticketId: string
  onBack?: () => void
  /** 关联工单 / 子工单 / 拆分结果跳转。 */
  onOpenTicket?: (ticketId: string) => void
}

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30'

const TEXTAREA_CLASS =
  'min-h-[80px] w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30'

/** 解决/关闭时顺带把解决方案写进去，避免后续复盘缺信息。 */
const RESOLUTION_TARGETS = new Set(['resolved', 'closed'])

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-gray-400 dark:text-gray-500">{label}</dt>
      <dd className="mt-0.5 truncate text-sm text-gray-800 dark:text-gray-200">{value}</dd>
    </div>
  )
}

/** 分派：PATCH assigneeId。服务端会校验目标用户是否存在。 */
function AssignDialog({
  open,
  onOpenChange,
  assigneeId,
  saving,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  assigneeId?: string | null
  saving: boolean
  onSubmit: (assigneeId: string) => Promise<{ ok: boolean; message: string }>
}) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setDraft(assigneeId ?? '')
    setError('')
  }, [open, assigneeId])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const trimmed = draft.trim()
    if (trimmed.length === 0) {
      setError('请填写处理人用户 ID')
      return
    }

    const result = await onSubmit(trimmed)
    if (!result.ok) {
      setError(result.message)
      return
    }
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>分派工单</DialogTitle>
          <DialogDescription>
            填写处理人的用户 ID。分派记录会写入工单活动时间线。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <label
              htmlFor="assign-ticket-user"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              处理人用户 ID
            </label>
            <Input
              id="assign-ticket-user"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="例如：u_1024"
              autoComplete="off"
              required
              disabled={saving}
            />
          </div>

          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}

          <DialogFooter className="gap-2 sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              取消
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <UserPlus className="mr-1.5 h-4 w-4" />
              )}
              确认分派
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 编辑基础字段。
 *
 * 刻意不提供优先级：契约里优先级由服务端按影响 × 紧急推导，前端手改会和服务端规则打架。
 */
function EditDialog({
  open,
  onOpenChange,
  ticket,
  meta,
  saving,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  ticket: TicketDetail
  meta: TicketMeta | null
  saving: boolean
  onSubmit: (payload: UpdateTicketRequest) => Promise<{ ok: boolean; message: string }>
}) {
  const [title, setTitle] = useState(ticket.title)
  const [description, setDescription] = useState(ticket.description)
  const [type, setType] = useState<string>(ticket.type)
  const [impact, setImpact] = useState<string>(ticket.impact)
  const [urgency, setUrgency] = useState<string>(ticket.urgency)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setTitle(ticket.title)
    setDescription(ticket.description)
    setType(ticket.type)
    setImpact(ticket.impact)
    setUrgency(ticket.urgency)
    setError('')
  }, [open, ticket])

  const types: string[] = meta?.types?.length ? meta.types : [...TICKET_TYPES]
  const impactLevels: string[] = meta?.impactLevels?.length
    ? meta.impactLevels
    : [...TICKET_IMPACT_LEVELS]
  const urgencyLevels: string[] = meta?.urgencyLevels?.length
    ? meta.urgencyLevels
    : [...TICKET_URGENCY_LEVELS]

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')

    if (title.trim().length === 0) {
      setError('标题不能为空')
      return
    }
    if (description.trim().length === 0) {
      setError('描述不能为空')
      return
    }

    const result = await onSubmit({
      title: title.trim(),
      description: description.trim(),
      type: type as TicketType,
      impact: impact as TicketImpact,
      urgency: urgency as TicketUrgency,
    })
    if (!result.ok) {
      setError(result.message)
      return
    }
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>编辑工单</DialogTitle>
          <DialogDescription>
            只提交发生变化的字段；状态请用状态流转，优先级由服务端推导。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <label
              htmlFor="edit-ticket-title"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              标题
            </label>
            <Input
              id="edit-ticket-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={200}
              required
              disabled={saving}
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="edit-ticket-description"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              描述
            </label>
            <textarea
              id="edit-ticket-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className={TEXTAREA_CLASS}
              required
              disabled={saving}
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="edit-ticket-type"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              工单类型
            </label>
            <select
              id="edit-ticket-type"
              value={type}
              onChange={(event) => setType(event.target.value)}
              className={SELECT_CLASS}
              disabled={saving}
            >
              {types.map((item) => (
                <option key={item} value={item}>
                  {ticketTypeLabel(item)}
                </option>
              ))}
            </select>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label
                htmlFor="edit-ticket-impact"
                className="text-xs text-gray-500 dark:text-gray-400"
              >
                影响范围
              </label>
              <select
                id="edit-ticket-impact"
                value={impact}
                onChange={(event) => setImpact(event.target.value)}
                className={SELECT_CLASS}
                disabled={saving}
              >
                {impactLevels.map((level) => (
                  <option key={level} value={level}>
                    {TICKET_IMPACT_LABELS[level] ?? level}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="edit-ticket-urgency"
                className="text-xs text-gray-500 dark:text-gray-400"
              >
                紧急程度
              </label>
              <select
                id="edit-ticket-urgency"
                value={urgency}
                onChange={(event) => setUrgency(event.target.value)}
                className={SELECT_CLASS}
                disabled={saving}
              >
                {urgencyLevels.map((level) => (
                  <option key={level} value={level}>
                    {TICKET_URGENCY_LABELS[level] ?? level}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <p className="text-xs text-gray-400 dark:text-gray-500">
            修改影响或紧急程度后，优先级由服务端重新推导。
          </p>

          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}

          <DialogFooter className="gap-2 sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              取消
            </Button>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function TicketDetailPanel({
  ticketId,
  onBack,
  onOpenTicket,
}: TicketDetailPanelProps) {
  const user = useAuthStore((state) => state.user)

  const ticket = useTicketStore((state) => state.current)
  const loading = useTicketStore((state) => state.detailLoading)
  const error = useTicketStore((state) => state.detailError)
  const detailStatus = useTicketStore((state) => state.detailStatus)
  const meta = useTicketStore((state) => state.meta)
  const metaError = useTicketStore((state) => state.metaError)
  const saving = useTicketStore((state) => state.saving)
  const transitioning = useTicketStore((state) => state.transitioning)
  const commenting = useTicketStore((state) => state.commenting)
  const linking = useTicketStore((state) => state.linking)
  const unlinking = useTicketStore((state) => state.unlinking)
  const merging = useTicketStore((state) => state.merging)
  const splitting = useTicketStore((state) => state.splitting)

  const loadDetail = useTicketStore((state) => state.loadDetail)
  const loadMeta = useTicketStore((state) => state.loadMeta)
  const refreshDetail = useTicketStore((state) => state.refreshDetail)
  const clearDetail = useTicketStore((state) => state.clearDetail)
  const updateTicket = useTicketStore((state) => state.updateTicket)
  const transitionTicket = useTicketStore((state) => state.transitionTicket)
  const addComment = useTicketStore((state) => state.addComment)
  const addLink = useTicketStore((state) => state.addLink)
  const removeLink = useTicketStore((state) => state.removeLink)
  const merge = useTicketStore((state) => state.merge)
  const split = useTicketStore((state) => state.split)

  const [editOpen, setEditOpen] = useState(false)
  const [assignOpen, setAssignOpen] = useState(false)
  const [pendingStatus, setPendingStatus] = useState<string | null>(null)
  const [transitionComment, setTransitionComment] = useState('')
  const [transitionResolution, setTransitionResolution] = useState('')
  const [transitionError, setTransitionError] = useState('')

  useEffect(() => {
    void loadMeta()
    void loadDetail(ticketId)
  }, [ticketId, loadDetail, loadMeta])

  // 离开页面时清掉详情，避免下次进来先闪一眼上一个工单。
  useEffect(() => () => clearDetail(), [clearDetail])

  // 切换工单时收起所有临时状态。
  useEffect(() => {
    setPendingStatus(null)
    setTransitionComment('')
    setTransitionResolution('')
    setTransitionError('')
    setEditOpen(false)
    setAssignOpen(false)
  }, [ticketId])

  const isAgent = canActAsAgent(user?.roles, meta?.agentRoles)
  const isMerged = Boolean(ticket?.mergedIntoId)

  /**
   * 非处理人绝不展示内部备注。
   *
   * 服务端已经按可见性过滤过一遍，这里再兜一层：即使载荷里混进内部备注，
   * 也不会在请求人的界面上出现，更不会被当成公开回复渲染。
   */
  const visibleComments = useMemo(
    () =>
      (ticket?.comments ?? []).filter(
        (comment) => isAgent || !isInternalComment(comment)
      ),
    [ticket?.comments, isAgent]
  )

  const transitions = useMemo(
    () => (ticket ? allowedTransitions(meta, ticket.status) : []),
    [meta, ticket]
  )

  async function handleUpdate(payload: UpdateTicketRequest) {
    if (!ticket) return { ok: false, message: '工单尚未加载' }
    const result = await updateTicket(ticket.id, payload)
    if (result.ok) {
      toast.success(result.message)
    } else {
      toast.error(result.message)
    }
    return result
  }

  async function handleAssign(assigneeId: string) {
    if (!ticket) return { ok: false, message: '工单尚未加载' }
    const result = await updateTicket(ticket.id, { assigneeId })
    if (result.ok) {
      toast.success(`已分派给 ${assigneeId}`)
    } else {
      toast.error(result.message)
    }
    return result
  }

  async function handleTransition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!ticket || !pendingStatus) return
    setTransitionError('')

    const result = await transitionTicket(ticket.id, {
      status: pendingStatus,
      comment: transitionComment.trim() || undefined,
      resolution: transitionResolution.trim() || undefined,
    })

    if (!result.ok) {
      // INVALID_TRANSITION 等错误码已由 store 映射成中文，这里原样展示。
      setTransitionError(result.message)
      toast.error(result.message)
      return
    }

    toast.success(`已流转至 ${ticketStatusLabel(pendingStatus)}`)
    setPendingStatus(null)
    setTransitionComment('')
    setTransitionResolution('')
  }

  async function handleComment(payload: AddTicketCommentRequest) {
    if (!ticket) return { ok: false, message: '工单尚未加载' }
    const result = await addComment(ticket.id, payload)
    if (result.ok) {
      toast.success(result.message)
    } else {
      toast.error(result.message)
    }
    return result
  }

  // 既没有数据也没有错误 = 正在加载（也覆盖开发模式 StrictMode 的重新挂载）。
  if (!ticket && !error) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-sm text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载工单…
      </div>
    )
  }

  // 服务端对"不可见"的工单返回 404：页面不能透露工单是否存在，也不渲染任何工单内容。
  if (!ticket) {
    const notFound = detailStatus === 404
    return (
      <div className="mx-auto max-w-xl px-6 py-16">
        <div className="rounded-xl border border-gray-200 bg-white p-6 text-center dark:border-gray-800 dark:bg-gray-900">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
            <AlertCircle className="h-5 w-5" />
          </div>
          <h2 className="mt-3 text-base font-semibold text-gray-900 dark:text-gray-100">
            {notFound ? '工单不存在' : '工单加载失败'}
          </h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {notFound ? TICKET_NOT_FOUND_MESSAGE : (error ?? '工单加载失败，请稍后重试')}
          </p>
          {notFound && (
            <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
              服务端对无权查看的工单同样返回 404，因此这里无法判断该工单是否存在。
            </p>
          )}
          <div className="mt-4 flex justify-center gap-2">
            {!notFound && (
              <Button variant="outline" onClick={() => void refreshDetail()}>
                <RefreshCw className="mr-1.5 h-4 w-4" />
                重试
              </Button>
            )}
            {onBack && (
              <Button variant="outline" onClick={onBack}>
                <ArrowLeft className="mr-1.5 h-4 w-4" />
                返回工单列表
              </Button>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (!ticket) {
    return (
      <div className="flex items-center justify-center py-20 text-sm text-gray-400">
        暂无工单数据
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          {onBack && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onBack}
              aria-label="返回工单列表"
              className="mt-0.5 text-gray-500"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm text-gray-400 dark:text-gray-500">
                {formatTicketNumber(ticket.number)}
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
            <h2 className="mt-1 text-lg font-semibold break-words text-gray-900 dark:text-gray-100">
              {ticket.title}
            </h2>
            <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
              创建于 {formatTicketDateTime(ticket.createdAt)} · 更新于{' '}
              {formatTicketRelativeTime(ticket.updatedAt) || '—'}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refreshDetail()}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            )}
            刷新
          </Button>
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil className="mr-1.5 h-3.5 w-3.5" />
            编辑
          </Button>
        </div>
      </header>

      {isMerged && (
        <p className="flex items-start gap-2 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600 dark:bg-gray-900 dark:text-gray-300">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          该工单已被合并，状态流转与回复会被服务端拒绝；请到目标工单继续处理。
        </p>
      )}

      <section className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          <Field label="请求人" value={ticket.requesterId} />
          <Field
            label="处理人"
            value={
              <span className="flex items-center gap-2">
                {ticket.assigneeId ?? '未分派'}
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={() => setAssignOpen(true)}
                  className="text-blue-600 hover:text-blue-700 dark:text-blue-400"
                >
                  分派
                </Button>
              </span>
            }
          />
          <Field label="来源" value={ticketSourceLabel(ticket.source)} />
          <Field
            label="影响范围"
            value={TICKET_IMPACT_LABELS[ticket.impact] ?? ticket.impact}
          />
          <Field
            label="紧急程度"
            value={TICKET_URGENCY_LABELS[ticket.urgency] ?? ticket.urgency}
          />
          <Field
            label="首次响应"
            value={
              ticket.firstResponseAt
                ? formatTicketDateTime(ticket.firstResponseAt)
                : '尚未响应'
            }
          />
          <Field
            label="解决时间"
            value={ticket.resolvedAt ? formatTicketDateTime(ticket.resolvedAt) : '—'}
          />
          <Field
            label="关闭时间"
            value={ticket.closedAt ? formatTicketDateTime(ticket.closedAt) : '—'}
          />
          <Field label="创建人" value={ticket.createdBy} />
        </dl>

        {(ticket.parentId || ticket.mergedIntoId || ticket.conversationId) && (
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-gray-100 pt-3 text-xs text-gray-500 dark:border-gray-800 dark:text-gray-400">
            {ticket.parentId && (
              <span className="inline-flex items-center gap-1">
                父工单：
                {onOpenTicket ? (
                  <button
                    type="button"
                    onClick={() => onOpenTicket(ticket.parentId as string)}
                    className="text-blue-600 hover:underline dark:text-blue-400"
                  >
                    {ticket.parentId}
                  </button>
                ) : (
                  ticket.parentId
                )}
              </span>
            )}
            {ticket.mergedIntoId && (
              <span className="inline-flex items-center gap-1">
                已合并到：
                {onOpenTicket ? (
                  <button
                    type="button"
                    onClick={() => onOpenTicket(ticket.mergedIntoId as string)}
                    className="text-blue-600 hover:underline dark:text-blue-400"
                  >
                    {ticket.mergedIntoId}
                  </button>
                ) : (
                  ticket.mergedIntoId
                )}
              </span>
            )}
            {ticket.conversationId && (
              <Link
                to={`/chat/${ticket.conversationId}`}
                className="inline-flex items-center gap-1 text-blue-600 hover:underline dark:text-blue-400"
              >
                <MessagesSquare className="h-3 w-3" />
                查看来源会话
              </Link>
            )}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">问题描述</h4>
        <p className="rounded-xl bg-gray-50 px-3 py-2 text-sm whitespace-pre-wrap text-gray-700 dark:bg-gray-900 dark:text-gray-300">
          {ticket.description}
        </p>
      </section>

      {ticket.resolution && (
        <section className="space-y-2">
          <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">解决方案</h4>
          <p className="rounded-xl bg-green-50 px-3 py-2 text-sm whitespace-pre-wrap text-green-900 dark:bg-green-950/30 dark:text-green-200">
            {ticket.resolution}
          </p>
        </section>
      )}

      <section className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-800">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">
            状态流转
          </h4>
          <p className="text-xs text-gray-400 dark:text-gray-500">
            当前：{ticketStatusLabel(ticket.status)} · 可选动作由服务端状态机给出
          </p>
        </div>

        {metaError && (
          <p className="flex flex-wrap items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-400">
            {metaError}
            <Button
              variant="ghost"
              size="xs"
              onClick={() => void loadMeta()}
              className="text-amber-700 dark:text-amber-400"
            >
              重试
            </Button>
          </p>
        )}

        {transitions.length === 0 ? (
          <p className="text-xs text-gray-400 dark:text-gray-500">
            {isMerged
              ? '已合并的工单不再提供状态流转。'
              : '当前状态没有可用的下一步动作。'}
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {transitions.map((status) => (
              <Button
                key={status}
                variant="outline"
                size="sm"
                disabled={isMerged || Boolean(transitioning[status])}
                onClick={() => {
                  setPendingStatus(status)
                  setTransitionComment('')
                  setTransitionResolution('')
                  setTransitionError('')
                }}
              >
                {transitioning[status] && (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                )}
                流转至 {ticketStatusLabel(status)}
              </Button>
            ))}
          </div>
        )}

        {pendingStatus && (
          <form
            onSubmit={handleTransition}
            className="space-y-2 rounded-lg border border-blue-200 bg-blue-50/50 p-3 dark:border-blue-900 dark:bg-blue-950/20"
          >
            <p className="text-xs text-gray-600 dark:text-gray-300">
              即将把状态改为{' '}
              <span className="font-medium">{ticketStatusLabel(pendingStatus)}</span>
            </p>

            <div className="space-y-1.5">
              <label
                htmlFor="transition-comment"
                className="text-xs text-gray-500 dark:text-gray-400"
              >
                流转备注（选填）
              </label>
              <Input
                id="transition-comment"
                value={transitionComment}
                onChange={(event) => setTransitionComment(event.target.value)}
                placeholder="例如：已联系用户确认，问题复现环境已固定"
                maxLength={500}
                disabled={Boolean(transitioning[pendingStatus])}
              />
            </div>

            {RESOLUTION_TARGETS.has(pendingStatus) && (
              <div className="space-y-1.5">
                <label
                  htmlFor="transition-resolution"
                  className="text-xs text-gray-500 dark:text-gray-400"
                >
                  解决方案（解决/关闭时建议填写）
                </label>
                <textarea
                  id="transition-resolution"
                  value={transitionResolution}
                  onChange={(event) => setTransitionResolution(event.target.value)}
                  placeholder="根因与处理方式，便于后续复盘"
                  className={TEXTAREA_CLASS}
                  disabled={Boolean(transitioning[pendingStatus])}
                />
              </div>
            )}

            {transitionError && (
              <p className="text-destructive text-sm" role="alert">
                {transitionError}
              </p>
            )}

            <div className="flex gap-2">
              <Button
                type="submit"
                size="sm"
                disabled={Boolean(transitioning[pendingStatus])}
              >
                {transitioning[pendingStatus] ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : null}
                确认流转
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setPendingStatus(null)
                  setTransitionError('')
                }}
              >
                取消
              </Button>
            </div>
          </form>
        )}
      </section>

      {ticket.children.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">
            子工单（{ticket.children.length}）
          </h4>
          <ul className="space-y-1.5">
            {ticket.children.map((child) => (
              <li key={child.id}>
                <button
                  type="button"
                  onClick={() => onOpenTicket?.(child.id)}
                  className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/40 dark:border-gray-800 dark:hover:border-blue-800 dark:hover:bg-blue-950/20"
                >
                  <span className="font-mono text-xs text-gray-400 dark:text-gray-500">
                    {formatTicketNumber(child.number)}
                  </span>
                  <span className="truncate text-sm text-gray-800 dark:text-gray-200">
                    {child.title}
                  </span>
                  <TicketTypeBadge type={child.type} />
                  <TicketStatusBadge status={child.status} />
                  <TicketPriorityBadge priority={child.priority} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <TicketLinksPanel
        ticket={ticket}
        meta={meta}
        comments={visibleComments}
        linking={linking}
        unlinking={unlinking}
        merging={merging}
        splitting={splitting}
        onAddLink={(payload) => addLink(ticket.id, payload)}
        onRemoveLink={(linkId) => removeLink(ticket.id, linkId)}
        onMerge={(targetId) => merge(ticket.id, targetId)}
        onSplit={(payload) => split(ticket.id, payload)}
        onOpenTicket={onOpenTicket}
      />

      <section className="space-y-3">
        <TicketTimelineHeader />
        <TicketTimeline
          comments={visibleComments}
          activities={ticket.activities ?? []}
          loading={loading && (ticket.activities ?? []).length === 0}
        />
      </section>

      <section className="space-y-2 border-t border-gray-200 pt-4 dark:border-gray-800">
        <TicketCommentComposer
          canUseInternal={isAgent}
          submitting={commenting}
          disabled={isMerged}
          onSubmit={handleComment}
        />
        {!isAgent && (
          <p className="text-xs text-gray-400 dark:text-gray-500">
            当前账号不是工单处理人，只能提交公开回复。
          </p>
        )}
      </section>

      <AssignDialog
        open={assignOpen}
        onOpenChange={setAssignOpen}
        assigneeId={ticket.assigneeId}
        saving={saving}
        onSubmit={handleAssign}
      />

      <EditDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        ticket={ticket}
        meta={meta}
        saving={saving}
        onSubmit={handleUpdate}
      />
    </div>
  )
}
