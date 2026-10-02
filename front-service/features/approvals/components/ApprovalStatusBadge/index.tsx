// features/approvals/components/ApprovalStatusBadge/index.tsx
import { CheckCircle2, Clock, XCircle } from 'lucide-react'
import type { ApprovalStatus, ResumeState } from '@/features/approvals/types'

const STATUS_STYLES: Record<
  ApprovalStatus,
  { label: string; className: string; icon: typeof Clock }
> = {
  pending: {
    label: '待审批',
    className:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-900',
    icon: Clock,
  },
  approved: {
    label: '已通过',
    className:
      'bg-green-50 text-green-700 border-green-200 dark:bg-green-950/40 dark:text-green-400 dark:border-green-900',
    icon: CheckCircle2,
  },
  rejected: {
    label: '已驳回',
    className:
      'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-400 dark:border-red-900',
    icon: XCircle,
  },
}

/**
 * agent 侧执行恢复的状态。审批结论只是第一步：真正的高风险动作要等
 * LangGraph 的 checkpoint 恢复成功才算落地，所以这个状态必须对用户可见。
 */
const RESUME_LABELS: Record<ResumeState, { label: string; className: string }> = {
  queued: {
    label: '执行待恢复',
    className: 'text-gray-500 dark:text-gray-400',
  },
  running: {
    label: '执行恢复中',
    className: 'text-blue-600 dark:text-blue-400',
  },
  succeeded: {
    label: '执行已完成',
    className: 'text-green-600 dark:text-green-400',
  },
  failed: {
    label: '执行失败（将重试）',
    className: 'text-red-600 dark:text-red-400',
  },
  stale: {
    label: '执行现场失效',
    className: 'text-gray-500 dark:text-gray-400 line-through',
  },
}

export function ApprovalStatusBadge({ status }: { status: ApprovalStatus }) {
  const config = STATUS_STYLES[status] ?? STATUS_STYLES.pending
  const Icon = config.icon

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${config.className}`}
    >
      <Icon className="h-3 w-3" />
      {config.label}
    </span>
  )
}

export function ResumeStateBadge({
  resumeState,
  className = '',
}: {
  resumeState?: ResumeState | null
  className?: string
}) {
  if (!resumeState) {
    return null
  }
  const config = RESUME_LABELS[resumeState] ?? {
    label: resumeState,
    className: 'text-gray-500',
  }

  return (
    <span className={`inline-flex items-center gap-1 text-xs ${config.className} ${className}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
      {config.label}
    </span>
  )
}

/** 风险等级用颜色而非文字冗余表达，和聊天里的高风险提示保持一致。 */
export function RiskBadge({ riskLevel }: { riskLevel?: string | null }) {
  const level = (riskLevel ?? 'low').toLowerCase()
  const styles =
    level === 'high'
      ? 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-400 dark:border-red-900'
      : level === 'medium'
        ? 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-900'
        : 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-gray-900 dark:text-gray-400 dark:border-gray-800'

  const label = level === 'high' ? '高风险' : level === 'medium' ? '中风险' : '低风险'

  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${styles}`}
    >
      {label}
    </span>
  )
}
