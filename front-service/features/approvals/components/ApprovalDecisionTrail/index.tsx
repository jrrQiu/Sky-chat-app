// features/approvals/components/ApprovalDecisionTrail/index.tsx
import { CheckCircle2, History, XCircle } from 'lucide-react'
import type { ApprovalDecision } from '@/features/approvals/types'

interface ApprovalDecisionTrailProps {
  decisions: ApprovalDecision[]
  loading?: boolean
}

function formatTime(value?: string | null): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * 追加式轨迹：审批行只保留最新状态，这里回答"是谁、以什么角色、为什么"做出的决定。
 * 这是合规要求，不是装饰，所以即使只有一条也照常显示。
 */
export function ApprovalDecisionTrail({
  decisions,
  loading = false,
}: ApprovalDecisionTrailProps) {
  if (loading) {
    return (
      <p className="py-3 text-xs text-gray-400 dark:text-gray-500">正在加载审批记录…</p>
    )
  }

  if (decisions.length === 0) {
    return (
      <p className="py-3 text-xs text-gray-400 dark:text-gray-500">
        尚无审批记录：该申请仍在等待决策。
      </p>
    )
  }

  return (
    <ol className="relative space-y-4 border-l border-gray-200 pl-4 dark:border-gray-800">
      {decisions.map((decision) => {
        const approved = decision.decision === 'approved'
        const Icon = approved ? CheckCircle2 : XCircle
        return (
          <li key={decision.id} className="relative">
            <span
              className={`absolute -left-[25px] flex h-4 w-4 items-center justify-center rounded-full bg-white dark:bg-gray-950 ${
                approved ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
              }`}
            >
              <Icon className="h-4 w-4" />
            </span>
            <p className="text-sm text-gray-900 dark:text-gray-100">
              <span className="font-medium">{decision.decidedBy}</span>
              {approved ? ' 通过了该申请' : ' 驳回了该申请'}
              {decision.decidedByRoles ? (
                <span className="ml-2 text-xs text-gray-400 dark:text-gray-500">
                  （{decision.decidedByRoles}）
                </span>
              ) : null}
            </p>
            <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
              {formatTime(decision.createdAt)}
              {decision.sourceIp ? ` · 来源 ${decision.sourceIp}` : ''}
            </p>
            {decision.comment && (
              <p className="mt-1.5 rounded-md bg-gray-50 px-3 py-2 text-xs whitespace-pre-wrap text-gray-700 dark:bg-gray-900 dark:text-gray-300">
                {decision.comment}
              </p>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export function ApprovalDecisionTrailHeader() {
  return (
    <h4 className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
      <History className="h-4 w-4 text-gray-400" />
      审批记录
    </h4>
  )
}
