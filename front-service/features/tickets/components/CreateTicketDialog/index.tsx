// features/tickets/components/CreateTicketDialog/index.tsx
import { useEffect, useState, type FormEvent } from 'react'
import { Loader2, Plus } from 'lucide-react'
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
import type { TicketCreateResult } from '@/features/tickets/store/ticket.store'
import {
  TICKET_IMPACT_LABELS,
  TICKET_IMPACT_LEVELS,
  TICKET_TYPES,
  TICKET_TYPE_LABELS,
  TICKET_URGENCY_LABELS,
  TICKET_URGENCY_LEVELS,
  ticketTypeLabel,
  type CreateTicketRequest,
  type TicketImpact,
  type TicketMeta,
  type TicketType,
  type TicketUrgency,
} from '@/features/tickets/types'

interface CreateTicketDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  meta: TicketMeta | null
  submitting: boolean
  onSubmit: (payload: CreateTicketRequest) => Promise<TicketCreateResult>
}

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30'

const TEXTAREA_CLASS =
  'min-h-[96px] w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30'

/**
 * 影响 × 紧急 三维矩阵选择器（ITIL 惯例）。
 *
 * 只让用户选这两个维度：优先级由服务端推导，界面不猜公式，避免和服务端算出不同的结果。
 */
function ImpactUrgencyMatrix({
  impactLevels,
  urgencyLevels,
  impact,
  urgency,
  disabled,
  onChange,
}: {
  impactLevels: string[]
  urgencyLevels: string[]
  impact: string
  urgency: string
  disabled?: boolean
  onChange: (impact: string, urgency: string) => void
}) {
  return (
    <div className="space-y-1.5">
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-1 text-xs">
          <thead>
            <tr>
              <th className="w-20 text-left font-normal text-gray-400 dark:text-gray-500">
                影响 \ 紧急
              </th>
              {urgencyLevels.map((level) => (
                <th
                  key={level}
                  className="font-normal text-gray-500 dark:text-gray-400"
                >
                  {TICKET_URGENCY_LABELS[level]?.split('（')[0] ?? level}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {impactLevels.map((impactLevel) => (
              <tr key={impactLevel}>
                <th className="text-left font-normal text-gray-500 dark:text-gray-400">
                  {TICKET_IMPACT_LABELS[impactLevel]?.split('（')[0] ?? impactLevel}
                </th>
                {urgencyLevels.map((urgencyLevel) => {
                  const selected = impact === impactLevel && urgency === urgencyLevel
                  return (
                    <td key={`${impactLevel}-${urgencyLevel}`}>
                      <button
                        type="button"
                        disabled={disabled}
                        aria-pressed={selected}
                        title={`影响 ${impactLevel} · 紧急 ${urgencyLevel}`}
                        onClick={() => onChange(impactLevel, urgencyLevel)}
                        className={`h-8 w-full rounded-md border text-xs transition-colors disabled:opacity-50 ${
                          selected
                            ? 'border-blue-400 bg-blue-100 text-blue-800 dark:border-blue-700 dark:bg-blue-900/50 dark:text-blue-300'
                            : 'border-gray-200 text-gray-300 hover:border-blue-300 hover:bg-blue-50/50 dark:border-gray-800 dark:text-gray-600 dark:hover:bg-blue-950/20'
                        }`}
                      >
                        {selected ? '已选' : '·'}
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        影响：{TICKET_IMPACT_LABELS[impact] ?? impact} · 紧急：
        {TICKET_URGENCY_LABELS[urgency] ?? urgency}
      </p>
    </div>
  )
}

export function CreateTicketDialog({
  open,
  onOpenChange,
  meta,
  submitting,
  onSubmit,
}: CreateTicketDialogProps) {
  const [type, setType] = useState<TicketType>('incident')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [impact, setImpact] = useState<string>('medium')
  const [urgency, setUrgency] = useState<string>('medium')
  const [assigneeId, setAssigneeId] = useState('')
  const [error, setError] = useState('')

  // 每次重新打开都回到干净状态，避免把上一个工单的内容带过来。
  useEffect(() => {
    if (!open) return
    setType('incident')
    setTitle('')
    setDescription('')
    setImpact('medium')
    setUrgency('medium')
    setAssigneeId('')
    setError('')
  }, [open])

  // meta 是权威来源，拿不到时才回退到内置枚举。
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
      setError('请填写工单标题')
      return
    }
    if (description.trim().length === 0) {
      setError('请填写问题描述，处理人需要据此判断')
      return
    }

    const result = await onSubmit({
      type,
      title: title.trim(),
      description: description.trim(),
      impact: impact as TicketImpact,
      urgency: urgency as TicketUrgency,
      assigneeId: assigneeId.trim() || undefined,
    })

    if (!result.ok) {
      setError(result.message)
      return
    }
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !submitting && onOpenChange(next)}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建工单</DialogTitle>
          <DialogDescription>
            工单创建后会进入服务端状态机，处理人可在允许的流转之间推进状态。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label
                htmlFor="create-ticket-type"
                className="text-xs text-gray-500 dark:text-gray-400"
              >
                工单类型
              </label>
              <select
                id="create-ticket-type"
                value={type}
                onChange={(event) => setType(event.target.value as TicketType)}
                className={SELECT_CLASS}
                disabled={submitting}
              >
                {types.map((item) => (
                  <option key={item} value={item}>
                    {ticketTypeLabel(item)}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="create-ticket-assignee"
                className="text-xs text-gray-500 dark:text-gray-400"
              >
                指派给（用户 ID，可留空）
              </label>
              <Input
                id="create-ticket-assignee"
                value={assigneeId}
                onChange={(event) => setAssigneeId(event.target.value)}
                placeholder="留空则由服务端按规则分派"
                autoComplete="off"
                disabled={submitting}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="create-ticket-title"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              标题
            </label>
            <Input
              id="create-ticket-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：财务共享系统登录报 500"
              maxLength={200}
              required
              disabled={submitting}
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="create-ticket-description"
              className="text-xs text-gray-500 dark:text-gray-400"
            >
              问题描述
            </label>
            <textarea
              id="create-ticket-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="现象、影响范围、复现步骤、期望结果"
              className={TEXTAREA_CLASS}
              required
              disabled={submitting}
            />
          </div>

          <div className="space-y-1.5">
            <span className="text-xs text-gray-500 dark:text-gray-400">
              影响范围 × 紧急程度
            </span>
            <ImpactUrgencyMatrix
              impactLevels={impactLevels}
              urgencyLevels={urgencyLevels}
              impact={impact}
              urgency={urgency}
              disabled={submitting}
              onChange={(nextImpact, nextUrgency) => {
                setImpact(nextImpact)
                setUrgency(nextUrgency)
              }}
            />
            <p className="text-xs text-gray-400 dark:text-gray-500">
              优先级由服务端根据影响 × 紧急推导，无需手动选择。
            </p>
          </div>

          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}

          <DialogFooter className="gap-2 sm:justify-between">
            <p className="text-xs text-gray-400 dark:text-gray-500">
              服务端会对类型与状态流转再做一次校验
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="mr-1.5 h-4 w-4" />
                )}
                创建工单
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
