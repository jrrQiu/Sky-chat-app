// features/approvals/components/ApprovalDetailDialog/index.tsx
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Ban, Check, Loader2, ShieldCheck } from 'lucide-react'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  ApprovalStatusBadge,
  ResumeStateBadge,
  RiskBadge,
} from '@/features/approvals/components/ApprovalStatusBadge'
import {
  ApprovalDecisionTrail,
  ApprovalDecisionTrailHeader,
} from '@/features/approvals/components/ApprovalDecisionTrail'
import { useApprovalStore } from '@/features/approvals/store/approval.store'
import type { ApprovalTask } from '@/features/approvals/types'
import { useAuthStore } from '@/features/auth/store/auth.store'

interface ApprovalDetailDialogProps {
  approval: ApprovalTask | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onDecide: (
    approvalId: string,
    action: 'approve' | 'reject',
    comment?: string
  ) => Promise<{ ok: boolean; message: string }>
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleString('zh-CN')
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-gray-400 dark:text-gray-500">{label}</dt>
      <dd className="mt-0.5 truncate text-sm text-gray-800 dark:text-gray-200" title={String(value ?? '')}>
        {value ?? '—'}
      </dd>
    </div>
  )
}

export function ApprovalDetailDialog({
  approval,
  open,
  onOpenChange,
  onDecide,
}: ApprovalDetailDialogProps) {
  const user = useAuthStore((state) => state.user)
  const decisions = useApprovalStore((state) =>
    approval ? (state.decisions[approval.id] ?? []) : []
  )
  const decisionsLoading = useApprovalStore((state) => state.decisionsLoading)
  const deciding = useApprovalStore((state) => state.deciding)

  const [comment, setComment] = useState('')
  const [confirmingReject, setConfirmingReject] = useState(false)
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null)

  // 每次打开新的审批都重置表单，避免把上一条的意见带过来。
  useEffect(() => {
    setComment('')
    setConfirmingReject(false)
    setFeedback(null)
  }, [approval?.id])

  const isRequester = Boolean(user?.id) && user?.id === approval?.userId
  const requiredRoles = useMemo(
    () =>
      (approval?.requiredApproverRoles ?? '')
        .split(',')
        .map((role) => role.trim())
        .filter(Boolean),
    [approval?.requiredApproverRoles]
  )
  const heldRoles = user?.roles ?? []
  const hasRole =
    requiredRoles.length === 0 ||
    heldRoles.includes('admin') ||
    requiredRoles.some((role) => heldRoles.includes(role))

  const isPending = approval?.status === 'pending'
  const canDecide = isPending && !isRequester && hasRole

  // 成熟产品不会把按钮藏起来，而是说明为什么不能点。
  const blockedReason = !isPending
    ? '该申请已有结论'
    : isRequester
      ? '不能审批自己发起的申请'
      : !hasRole
        ? `需要以下角色之一：${requiredRoles.join(' / ')}`
        : null

  async function submit(action: 'approve' | 'reject') {
    if (!approval) return
    if (action === 'reject' && comment.trim().length === 0) {
      setConfirmingReject(true)
      setFeedback({ ok: false, message: '驳回必须填写理由，便于后续审计追溯' })
      return
    }
    const result = await onDecide(approval.id, action, comment.trim() || undefined)
    setFeedback(result)
    if (result.ok) {
      setComment('')
      setConfirmingReject(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            审批详情
            {approval && <ApprovalStatusBadge status={approval.status} />}
            {approval && <RiskBadge riskLevel={approval.riskLevel} />}
          </DialogTitle>
          <DialogDescription>
            高风险操作需要人工确认。结论会写回审批记录，并由 Agent 恢复执行现场。
          </DialogDescription>
        </DialogHeader>

        {approval && (
          <ScrollArea className="max-h-[52vh] pr-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
              <Field label="申请人" value={approval.userId} />
              <Field label="审批角色" value={requiredRoles.join(' / ') || '不限'} />
              <Field label="Agent" value={approval.agentId} />
              <Field label="审批规则" value={approval.ruleId} />
              <Field label="意图" value={approval.intent ?? '—'} />
              <Field label="风险等级" value={approval.riskLevel ?? '—'} />
              <Field label="提交时间" value={formatTime(approval.createdAt)} />
              <Field
                label="决策时间"
                value={approval.decidedAt ? formatTime(approval.decidedAt) : '—'}
              />
              {approval.decidedBy && (
                <Field label="决策人" value={approval.decidedBy} />
              )}
              <Field
                label="执行现场"
                value={
                  approval.checkpointId
                    ? `${approval.checkpointId.slice(0, 8)}…`
                    : '未记录'
                }
              />
            </dl>

            <div className="mt-3">
              <ResumeStateBadge resumeState={approval.resumeState} />
              {approval.resumeError && (
                <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                  恢复错误：{approval.resumeError}
                </p>
              )}
            </div>

            {approval.decisionComment && (
              <div className="mt-4">
                <p className="text-xs text-gray-400 dark:text-gray-500">决策意见</p>
                <p className="mt-1 rounded-md bg-gray-50 px-3 py-2 text-sm whitespace-pre-wrap text-gray-700 dark:bg-gray-900 dark:text-gray-300">
                  {approval.decisionComment}
                </p>
              </div>
            )}

            <div className="mt-5">
              <ApprovalDecisionTrailHeader />
              <ApprovalDecisionTrail decisions={decisions} loading={decisionsLoading} />
            </div>

            {canDecide && (
              <div className="mt-5 space-y-2">
                <label
                  htmlFor="approval-comment"
                  className="text-xs text-gray-500 dark:text-gray-400"
                >
                  审批意见{confirmingReject ? '（驳回必填）' : '（选填）'}
                </label>
                <Input
                  id="approval-comment"
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  placeholder="例如：已核对申请人权限，同意开通"
                  maxLength={500}
                  disabled={deciding}
                />
              </div>
            )}

            {blockedReason && isPending && (
              <p className="mt-4 flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-400">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {blockedReason}
              </p>
            )}

            {feedback && (
              <p
                className={`mt-3 flex items-center gap-2 rounded-md px-3 py-2 text-xs ${
                  feedback.ok
                    ? 'bg-green-50 text-green-700 dark:bg-green-950/30 dark:text-green-400'
                    : 'bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-400'
                }`}
              >
                {feedback.ok ? (
                  <Check className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <Ban className="h-3.5 w-3.5 shrink-0" />
                )}
                {feedback.message}
              </p>
            )}
          </ScrollArea>
        )}

        <DialogFooter className="gap-2 sm:justify-between">
          <p className="flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-500">
            <ShieldCheck className="h-3.5 w-3.5" />
            服务端会再次校验职责分离与审批角色
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => void submit('reject')}
              disabled={!canDecide || deciding}
              className="border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950/30"
            >
              驳回
            </Button>
            <Button
              onClick={() => void submit('approve')}
              disabled={!canDecide || deciding}
            >
              {deciding ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Check className="mr-1.5 h-4 w-4" />
              )}
              通过
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
