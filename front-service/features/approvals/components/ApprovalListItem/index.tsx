// features/approvals/components/ApprovalListItem/index.tsx
import { ChevronRight, FileText, ShieldAlert, User } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import {
  ApprovalStatusBadge,
  ResumeStateBadge,
  RiskBadge,
} from '@/features/approvals/components/ApprovalStatusBadge'
import type { ApprovalTask } from '@/features/approvals/types'

interface ApprovalListItemProps {
  approval: ApprovalTask
  /** 当前登录用户 id，用来区分"我发起的"和"待我审批"。 */
  currentUserId?: string
  onOpen: (approvalId: string) => void
}

const AGENT_LABELS: Record<string, string> = {
  network: '网络',
  finance: '财务',
  hr: '人事',
  it: 'IT',
  approval: '审批',
  knowledge: '知识',
}

/** 相对时间在列表里比绝对时间更好读；失败时回退到空字符串而不是抛错。 */
function relativeTime(value?: string | null): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  try {
    return formatDistanceToNow(parsed, { addSuffix: true, locale: zhCN })
  } catch {
    return ''
  }
}

export function ApprovalListItem({
  approval,
  currentUserId,
  onOpen,
}: ApprovalListItemProps) {
  const isMine = Boolean(currentUserId) && approval.userId === currentUserId
  const agentLabel = AGENT_LABELS[approval.agentId] ?? approval.agentId
  const roles = (approval.requiredApproverRoles ?? '')
    .split(',')
    .map((role) => role.trim())
    .filter(Boolean)

  return (
    <button
      type="button"
      onClick={() => onOpen(approval.id)}
      className="group flex w-full items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/40 focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:outline-none dark:border-gray-800 dark:bg-gray-900 dark:hover:border-blue-800 dark:hover:bg-blue-950/20"
    >
      <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
        <ShieldAlert className="h-4 w-4" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
            {agentLabel} Agent · {approval.ruleId}
          </span>
          <ApprovalStatusBadge status={approval.status} />
          <RiskBadge riskLevel={approval.riskLevel} />
          {isMine && (
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
              我发起的
            </span>
          )}
        </div>

        <p className="mt-1 truncate text-xs text-gray-500 dark:text-gray-400">
          {approval.intent ? `意图：${approval.intent} · ` : ''}
          编号 {approval.id}
        </p>

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
          <span className="inline-flex items-center gap-1">
            <User className="h-3 w-3" />
            {approval.userId}
          </span>
          {roles.length > 0 && (
            <span>
              审批角色：{roles.join(' / ')}
            </span>
          )}
          {approval.createdAt && <span>{relativeTime(approval.createdAt)}</span>}
          {approval.decidedBy && (
            <span className="inline-flex items-center gap-1">
              <FileText className="h-3 w-3" />
              {approval.decidedBy} 已处理
            </span>
          )}
        </div>

        <div className="mt-1.5">
          <ResumeStateBadge resumeState={approval.resumeState} />
        </div>
      </div>

      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-gray-300 transition-transform group-hover:translate-x-0.5 group-hover:text-blue-500 dark:text-gray-600" />
    </button>
  )
}
