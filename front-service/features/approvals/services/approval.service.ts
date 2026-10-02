// features/approvals/services/approval.service.ts
import { apiFetch, apiJson, readApiError } from '@/lib/api/client'
import type {
  ApprovalDecision,
  ApprovalScope,
  ApprovalTask,
  DecideApprovalRequest,
  DecideApprovalResponse,
} from '@/features/approvals/types'

const DEFAULT_LIMIT = 200

/**
 * 审批列表。
 *
 * scope 决定视图：`all` 是"与我相关"（我发起的 + 待我审批），`to_approve` 只取待我审批，
 * `mine` 只取我发起的。服务端对 `to_approve` 应用与决策接口完全相同的角色规则，
 * 因此界面上不会出现"点了却被拒绝"的按钮。
 */
export function listApprovals(
  scope: ApprovalScope = 'all',
  limit = DEFAULT_LIMIT
): Promise<ApprovalTask[]> {
  const query = new URLSearchParams({ scope, limit: String(limit) })
  return apiJson<ApprovalTask[]>(`/v1/approvals?${query.toString()}`)
}

/** 单个审批的追加式决策轨迹。 */
export function listApprovalDecisions(approvalId: string): Promise<ApprovalDecision[]> {
  return apiJson<ApprovalDecision[]>(
    `/v1/approvals/${encodeURIComponent(approvalId)}/decisions`
  )
}

/**
 * 提交审批结论。
 *
 * 不走 apiJson：403/409/429 都带有结构化错误码，需要把 `error` 字段原样交给
 * store 去映射成中文，而不是丢掉。
 */
export async function decideApproval(
  approvalId: string,
  payload: DecideApprovalRequest
): Promise<DecideApprovalResponse> {
  const response = await apiFetch(
    `/v1/approvals/${encodeURIComponent(approvalId)}/decision`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }
  )

  if (response.ok) {
    return (await response.json()) as DecideApprovalResponse
  }

  const error = await readApiError(response)
  return { error: error.message }
}
