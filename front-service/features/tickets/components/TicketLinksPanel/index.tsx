// features/tickets/components/TicketLinksPanel/index.tsx
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { GitMerge, Loader2, Plus, Split, Unlink, X } from 'lucide-react'
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
import { TicketStatusBadge } from '@/features/tickets/components/TicketStatusBadge'
import type {
  TicketActionResult,
  TicketMergeResult,
  TicketSplitResult,
} from '@/features/tickets/store/ticket.store'
import {
  formatTicketDateTime,
  formatTicketNumber,
  TICKET_LINK_TYPES,
  TICKET_LINK_TYPE_HINTS,
  ticketLinkDirectionLabel,
  ticketLinkTypeLabel,
  type AddTicketLinkRequest,
  type SplitTicketRequest,
  type TicketComment,
  type TicketDetail,
  type TicketLinkType,
  type TicketMeta,
} from '@/features/tickets/types'

interface TicketLinksPanelProps {
  ticket: TicketDetail
  meta: TicketMeta | null
  /** 当前用户可见的评论（非处理人已剔除内部备注），拆分时从这里选。 */
  comments: TicketComment[]
  linking: boolean
  unlinking: Record<string, boolean>
  merging: boolean
  splitting: boolean
  onAddLink: (payload: AddTicketLinkRequest) => Promise<TicketActionResult>
  onRemoveLink: (linkId: string) => Promise<TicketActionResult>
  onMerge: (targetId: string) => Promise<TicketMergeResult>
  onSplit: (payload: SplitTicketRequest) => Promise<TicketSplitResult>
  onOpenTicket?: (ticketId: string) => void
}

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30'

const TEXTAREA_CLASS =
  'min-h-[72px] w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30'

/** 合并：把本工单并入目标工单，服务端会校验自合并与环形合并。 */
function MergeDialog({
  open,
  onOpenChange,
  ticketNumber,
  merging,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  ticketNumber: number
  merging: boolean
  onSubmit: (targetId: string) => Promise<TicketMergeResult>
}) {
  const [targetId, setTargetId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setTargetId('')
    setError('')
  }, [open])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const trimmed = targetId.trim()
    if (trimmed.length === 0) {
      setError('请填写目标工单 ID')
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
    <Dialog open={open} onOpenChange={(next) => !merging && onOpenChange(next)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>合并工单</DialogTitle>
          <DialogDescription>
            将 {formatTicketNumber(ticketNumber)} 合并到目标工单。合并后本工单保留记录，
            但不再接受状态流转。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <label
              htmlFor="merge-target-id"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              目标工单 ID
            </label>
            <Input
              id="merge-target-id"
              value={targetId}
              onChange={(event) => setTargetId(event.target.value)}
              placeholder="工单 ID（详情页 /tickets/ 后面的那一段）"
              autoComplete="off"
              required
              disabled={merging}
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
              disabled={merging}
            >
              取消
            </Button>
            <Button type="submit" disabled={merging}>
              {merging ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <GitMerge className="mr-1.5 h-4 w-4" />
              )}
              确认合并
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** 拆分：从某条评论拆出一个新工单。 */
function SplitDialog({
  open,
  onOpenChange,
  comments,
  splitting,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  comments: TicketComment[]
  splitting: boolean
  onSubmit: (payload: SplitTicketRequest) => Promise<TicketSplitResult>
}) {
  const [commentId, setCommentId] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setCommentId('')
    setTitle('')
    setDescription('')
    setError('')
  }, [open])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')

    if (commentId.length === 0) {
      setError('请选择要拆出的回复')
      return
    }
    if (title.trim().length === 0) {
      setError('请填写新工单标题')
      return
    }

    const result = await onSubmit({
      commentId,
      title: title.trim(),
      description: description.trim() || undefined,
    })
    if (!result.ok) {
      setError(result.message)
      return
    }
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !splitting && onOpenChange(next)}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>拆分工单</DialogTitle>
          <DialogDescription>
            选中的回复会作为新工单的内容来源；原工单与评论保持不变。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <label
              htmlFor="split-comment"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              选择回复
            </label>
            <select
              id="split-comment"
              value={commentId}
              onChange={(event) => setCommentId(event.target.value)}
              className={SELECT_CLASS}
              disabled={splitting}
            >
              <option value="">请选择一条回复</option>
              {comments.map((comment) => (
                <option key={comment.id} value={comment.id}>
                  {comment.visibility === 'internal' ? '[内部] ' : ''}
                  {comment.authorId}：
                  {comment.body.length > 40 ? `${comment.body.slice(0, 40)}…` : comment.body}
                </option>
              ))}
            </select>
            {comments.length === 0 && (
              <p className="text-xs text-gray-400 dark:text-gray-500">
                当前没有可拆分的回复，请先添加一条公开回复。
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="split-title"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              新工单标题
            </label>
            <Input
              id="split-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：同一问题在财务系统复现"
              maxLength={200}
              required
              disabled={splitting}
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="split-description"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              补充描述（选填）
            </label>
            <textarea
              id="split-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="留空时服务端会沿用所选回复的内容"
              className={TEXTAREA_CLASS}
              disabled={splitting}
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
              disabled={splitting}
            >
              取消
            </Button>
            <Button type="submit" disabled={splitting || comments.length === 0}>
              {splitting ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Split className="mr-1.5 h-4 w-4" />
              )}
              确认拆分
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 关联工单面板。
 *
 * direction 必须原样展示：incoming 表示"对端声明的关联"，本工单只是被动方；
 * 混在一起会让人误以为两条关联是同一件事。
 */
export function TicketLinksPanel({
  ticket,
  meta,
  comments,
  linking,
  unlinking,
  merging,
  splitting,
  onAddLink,
  onRemoveLink,
  onMerge,
  onSplit,
  onOpenTicket,
}: TicketLinksPanelProps) {
  const [addOpen, setAddOpen] = useState(false)
  const [targetId, setTargetId] = useState('')
  const [linkType, setLinkType] = useState<string>('related')
  const [addError, setAddError] = useState('')
  const [mergeOpen, setMergeOpen] = useState(false)
  const [splitOpen, setSplitOpen] = useState(false)
  const [removeError, setRemoveError] = useState('')

  const linkTypes: string[] = useMemo(
    () => (meta?.linkTypes?.length ? meta.linkTypes : [...TICKET_LINK_TYPES]),
    [meta?.linkTypes]
  )

  const links = ticket.links ?? []

  const handleAdd = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setAddError('')
    const trimmed = targetId.trim()
    if (trimmed.length === 0) {
      setAddError('请填写目标工单 ID')
      return
    }

    const result = await onAddLink({
      targetId: trimmed,
      linkType: linkType as TicketLinkType,
    })
    if (!result.ok) {
      setAddError(result.message)
      return
    }
    setTargetId('')
    setLinkType('related')
    setAddOpen(false)
  }

  const handleRemove = async (linkId: string) => {
    setRemoveError('')
    const result = await onRemoveLink(linkId)
    if (!result.ok) {
      setRemoveError(result.message)
    }
  }

  const handleMerge = async (id: string) => {
    const result = await onMerge(id)
    if (result.ok && result.target) {
      onOpenTicket?.(result.target.id)
    }
    return result
  }

  const handleSplit = async (payload: SplitTicketRequest) => {
    const result = await onSplit(payload)
    if (result.ok && result.ticket) {
      onOpenTicket?.(result.ticket.id)
    }
    return result
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
          <GitMerge className="h-4 w-4 text-gray-400" />
          关联工单
          <span className="text-xs font-normal text-gray-400 dark:text-gray-500">
            {links.length} 条
          </span>
        </h4>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setAddOpen((open) => !open)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            添加关联
          </Button>
          <Button variant="outline" size="sm" onClick={() => setMergeOpen(true)}>
            <GitMerge className="mr-1.5 h-3.5 w-3.5" />
            合并
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSplitOpen(true)}>
            <Split className="mr-1.5 h-3.5 w-3.5" />
            拆分
          </Button>
        </div>
      </div>

      {addOpen && (
        <form
          onSubmit={handleAdd}
          className="flex flex-wrap items-end gap-2 rounded-xl border border-gray-200 bg-gray-50/60 p-3 dark:border-gray-800 dark:bg-gray-900/40"
        >
          <div className="min-w-[200px] flex-1 space-y-1.5">
            <label
              htmlFor="link-target-id"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              目标工单 ID
            </label>
            <Input
              id="link-target-id"
              value={targetId}
              onChange={(event) => setTargetId(event.target.value)}
              placeholder="工单 ID（不是编号 #1042）"
              autoComplete="off"
              disabled={linking}
            />
          </div>

          <div className="w-40 space-y-1.5">
            <label
              htmlFor="link-type"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              关联类型
            </label>
            <select
              id="link-type"
              value={linkType}
              onChange={(event) => setLinkType(event.target.value)}
              className={SELECT_CLASS}
              disabled={linking}
            >
              {linkTypes.map((item) => (
                <option key={item} value={item}>
                  {ticketLinkTypeLabel(item)}
                </option>
              ))}
            </select>
          </div>

          <Button type="submit" size="sm" disabled={linking}>
            {linking ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="mr-1.5 h-3.5 w-3.5" />
            )}
            添加
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setAddOpen(false)
              setAddError('')
            }}
          >
            <X className="mr-1.5 h-3.5 w-3.5" />
            收起
          </Button>

          {addError && (
            <p className="text-destructive w-full text-sm" role="alert">
              {addError}
            </p>
          )}
          <p className="w-full text-xs text-gray-400 dark:text-gray-500">
            {TICKET_LINK_TYPE_HINTS[linkType] ?? '关联关系由服务端校验方向与重复性'}
          </p>
        </form>
      )}

      {removeError && (
        <p className="text-destructive text-sm" role="alert">
          {removeError}
        </p>
      )}

      {links.length === 0 ? (
        <p className="rounded-xl border border-dashed border-gray-200 px-3 py-4 text-xs text-gray-400 dark:border-gray-800 dark:text-gray-500">
          暂无关联工单。重复报障、上下游依赖都可以通过关联串起来。
        </p>
      ) : (
        <ul className="space-y-2">
          {links.map((link) => (
            <li
              key={link.id}
              className="flex flex-wrap items-start justify-between gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2.5 dark:border-gray-800 dark:bg-gray-900"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                    {ticketLinkTypeLabel(link.linkType)}
                  </span>
                  <span className="rounded border border-gray-200 px-1.5 py-0.5 text-[11px] text-gray-500 dark:border-gray-800 dark:text-gray-400">
                    {ticketLinkDirectionLabel(link.direction)}
                  </span>
                  {link.linkedStatus && (
                    <TicketStatusBadge status={link.linkedStatus} />
                  )}
                </div>

                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-gray-400 dark:text-gray-500">
                    {formatTicketNumber(link.linkedNumber)}
                  </span>
                  {onOpenTicket ? (
                    <button
                      type="button"
                      onClick={() => onOpenTicket(link.linkedTicketId)}
                      className="truncate text-sm text-blue-600 hover:underline dark:text-blue-400"
                    >
                      {link.linkedTitle ?? link.linkedTicketId}
                    </button>
                  ) : (
                    <span className="truncate text-sm text-gray-700 dark:text-gray-300">
                      {link.linkedTitle ?? link.linkedTicketId}
                    </span>
                  )}
                </div>

                <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
                  {link.createdBy} 添加于 {formatTicketDateTime(link.createdAt)}
                </p>
              </div>

              <Button
                variant="ghost"
                size="sm"
                onClick={() => void handleRemove(link.id)}
                disabled={Boolean(unlinking[link.id])}
                className="text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
                aria-label="移除关联"
              >
                {unlinking[link.id] ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Unlink className="h-3.5 w-3.5" />
                )}
                移除
              </Button>
            </li>
          ))}
        </ul>
      )}

      <MergeDialog
        open={mergeOpen}
        onOpenChange={setMergeOpen}
        ticketNumber={ticket.number}
        merging={merging}
        onSubmit={handleMerge}
      />

      <SplitDialog
        open={splitOpen}
        onOpenChange={setSplitOpen}
        comments={comments}
        splitting={splitting}
        onSubmit={handleSplit}
      />
    </section>
  )
}
