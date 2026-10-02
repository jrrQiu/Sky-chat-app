// features/approvals/types.ts
// 审批领域类型：与 java-service 的 ApprovalTask / ApprovalDecision 一一对应

export type ApprovalStatus = 'pending' | 'approved' | 'rejected'

export type ResumeState = 'queued' | 'running' | 'succeeded' | 'failed' | 'stale'

export type ApprovalScope = 'all' | 'to_approve' | 'mine'

export type ApprovalStatusFilter = 'all' | ApprovalStatus

/** agent 侧 checkpoint 恢复的当前状态，用于向用户解释"通过之后发生了什么"。 */
export interface ApprovalTask {
  id: string
  runId: string
  turnId: string
  threadId: string
  checkpointId?: string | null
  checkpointNs?: string | null
  interruptId?: string | null
  userId: string
  agentId: string
  intent?: string | null
  riskLevel?: string | null
  ruleId: string
  requiredApproverRoles?: string | null
  status: ApprovalStatus
  resumeState?: ResumeState | null
  resumeAttempts?: number | null
  resumeError?: string | null
  resumeRequestId?: string | null
  decidedBy?: string | null
  decidedAt?: string | null
  decisionComment?: string | null
  createdAt?: string | null
  updatedAt?: string | null
  resumedAt?: string | null
}

/** 追加式决策轨迹：谁、何时、以什么角色、为什么。 */
export interface ApprovalDecision {
  id: string
  approvalId: string
  decision: 'approved' | 'rejected'
  decidedBy: string
  decidedByRoles?: string | null
  comment?: string | null
  sourceIp?: string | null
  resumeRequestId?: string | null
  createdAt?: string | null
}

export interface DecideApprovalRequest {
  action: 'approve' | 'reject'
  comment?: string
}

export interface DecideApprovalResponse {
  approval?: ApprovalTask
  resume?: {
    resumed?: boolean
    status?: string
    retryable?: boolean
    error?: string
  }
  error?: string
}

/** 服务端稳定错误码 → 中文提示，避免把后端码直接抛给用户。 */
export const APPROVAL_ERROR_MESSAGES: Record<string, string> = {
  SELF_APPROVAL_FORBIDDEN: '不能审批自己发起的申请，请由其他审批人处理',
  APPROVER_ROLE_REQUIRED: '当前账号缺少该审批所需的角色',
  APPROVAL_NOT_FOUND: '审批不存在或已被删除',
  APPROVAL_ALREADY_DECIDED: '该审批已有结论，无法重复处理',
  STALE_APPROVAL: '审批对应的执行现场已失效，无法恢复',
  CHECKPOINT_NOT_FOUND: '执行现场已丢失，无法恢复该审批',
  RESUME_IN_PROGRESS: '该审批正在恢复中，请稍后刷新',
  RATE_LIMITED: '操作过于频繁，请稍后重试',
}

export function approvalErrorMessage(code?: string | null): string {
  if (!code) {
    return '操作失败，请稍后重试'
  }
  return APPROVAL_ERROR_MESSAGES[code] ?? code
}
